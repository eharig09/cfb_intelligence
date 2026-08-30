"""Empirical in-house expected-drive-points model.

This is deliberately *not* labeled EPA. Version EDP-v1 estimates how many
points the offense will score on the current possession from down, distance and
field position. Provider PPA is never used to fit this model.

All historical fitting/scoring paths stream SQLite rows. This is intentional:
production runs on a memory-constrained host and a multi-season PBP sample can
contain hundreds of thousands of plays.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

MODEL_VERSION = "edp-v1"
MIN_CELL = 20
WRITE_BATCH = 1000


def initialize(repository) -> None:
    from sports_aggregator.cfb.play_by_play import initialize as initialize_pbp
    initialize_pbp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_expected_drive_points (
          model_version TEXT NOT NULL,
          down_bucket INTEGER NOT NULL,
          distance_bucket INTEGER NOT NULL,
          field_bucket INTEGER NOT NULL,
          samples INTEGER NOT NULL,
          expected_points REAL NOT NULL,
          fitted_at TEXT NOT NULL,
          PRIMARY KEY(model_version,down_bucket,distance_bucket,field_bucket)
        );
        CREATE TABLE IF NOT EXISTS cfb_play_value_metrics (
          play_id TEXT NOT NULL,
          model_version TEXT NOT NULL,
          edp_before REAL,
          edp_after REAL,
          edpa REAL,
          scored_at TEXT NOT NULL,
          PRIMARY KEY(play_id,model_version),
          FOREIGN KEY(play_id) REFERENCES cfb_plays(play_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_play_values_model
          ON cfb_play_value_metrics(model_version,play_id);
        """)
        connection.commit()


def _down(value: Any) -> int:
    try:
        down = int(value)
    except (TypeError, ValueError):
        return 0
    return down if 1 <= down <= 4 else 0


def _distance(value: Any) -> int:
    try:
        distance = max(0, int(value))
    except (TypeError, ValueError):
        return 0
    if distance <= 2:
        return 1
    if distance <= 5:
        return 2
    if distance <= 10:
        return 3
    if distance <= 15:
        return 4
    return 5


def _field(value: Any) -> int:
    try:
        ytg = max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return 0
    if ytg <= 10:
        return 1
    if ytg <= 20:
        return 2
    if ytg <= 40:
        return 3
    if ytg <= 60:
        return 4
    if ytg <= 80:
        return 5
    return 6


def state_key(row: Any) -> tuple[int, int, int]:
    return _down(row["down"]), _distance(row["distance"]), _field(row["yards_to_goal"])


def fit_model(repository, *, from_season: int | None = None,
              to_season: int | None = None, model_version: str = MODEL_VERSION,
              min_cell: int = MIN_CELL) -> dict[str, Any]:
    """Fit current-drive point expectation with constant-sized Python memory."""
    initialize(repository)
    where = ["p.drive_id IS NOT NULL", "d.points IS NOT NULL"]
    params: list[Any] = ["pbp-v1"]
    if from_season is not None:
        where.append("p.season>=?")
        params.append(int(from_season))
    if to_season is not None:
        where.append("p.season<=?")
        params.append(int(to_season))

    cells: dict[tuple[int, int, int], list[float]] = {}
    global_sum = 0.0
    global_count = 0
    with closing(repository._connect()) as connection:
        cursor = connection.execute(f"""
          SELECT p.down,p.distance,p.yards_to_goal,d.points
          FROM cfb_plays p
          JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version=?
          JOIN cfb_drive_metrics d ON d.game_id=p.game_id AND d.drive_id=p.drive_id
                               AND d.metric_version=m.metric_version
          WHERE {' AND '.join(where)} AND m.rush_pass IN ('rush','pass')
        """, params)
        for row in cursor:
            key = state_key(row)
            if 0 in key:
                continue
            target = float(row["points"] or 0)
            aggregate = cells.setdefault(key, [0.0, 0.0])
            aggregate[0] += target
            aggregate[1] += 1
            global_sum += target
            global_count += 1

    global_mean = global_sum / global_count if global_count else 0.0
    fitted_at = datetime.now(timezone.utc).isoformat()
    output = []
    for key, (cell_sum, raw_count) in cells.items():
        n = int(raw_count)
        raw = cell_sum / n
        weight = n / (n + max(1, int(min_cell)))
        estimate = weight * raw + (1.0 - weight) * global_mean
        output.append((model_version, *key, n, estimate, fitted_at))

    with closing(repository._connect()) as connection:
        connection.execute(
            "DELETE FROM cfb_expected_drive_points WHERE model_version=?", (model_version,))
        connection.executemany("""INSERT INTO cfb_expected_drive_points
          (model_version,down_bucket,distance_bucket,field_bucket,samples,expected_points,fitted_at)
          VALUES(?,?,?,?,?,?,?)""", output)
        connection.commit()
    return {
        "model_version": model_version,
        "plays": global_count,
        "cells": len(output),
        "global_mean": round(global_mean, 4),
        "from_season": from_season,
        "to_season": to_season,
    }


def _lookup(table: dict[tuple[int, int, int], tuple[float, int]],
            key: tuple[int, int, int]) -> float | None:
    if key in table:
        return table[key][0]
    down, _distance_bucket, field = key
    candidates = [(value, n) for (d, _dist, f), (value, n) in table.items()
                  if d == down and f == field]
    if candidates:
        total = sum(n for _, n in candidates)
        return sum(value * n for value, n in candidates) / total
    candidates = [(value, n) for (_d, _dist, f), (value, n) in table.items()
                  if f == field]
    if candidates:
        total = sum(n for _, n in candidates)
        return sum(value * n for value, n in candidates) / total
    return None


def _realized_points(row: Any) -> float:
    if not row["scoring"]:
        return 0.0
    text = str(row["play_type"] or "").casefold()
    if "field goal" in text:
        return 3.0
    if "two" in text:
        return 2.0
    if "touchdown" in text:
        return 6.0
    if "safety" in text:
        return 2.0
    return 0.0


def score_plays(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    """Score plays one drive at a time and write in bounded batches."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        model_rows = connection.execute("""SELECT down_bucket,distance_bucket,field_bucket,
          expected_points,samples FROM cfb_expected_drive_points WHERE model_version=?""",
          (model_version,)).fetchall()
    table = {
        (int(r[0]), int(r[1]), int(r[2])): (float(r[3]), int(r[4]))
        for r in model_rows
    }
    if not table:
        return {"model_version": model_version, "scored": 0, "reason": "model_not_fitted"}

    clauses = ["m.metric_version='pbp-v1'", "(m.rush_pass IN ('rush','pass') OR p.scoring=1)"]
    params: list[Any] = []
    season_clauses: list[str] = []
    season_params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?")
        params.append(int(from_season))
        season_clauses.append("season>=?")
        season_params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?")
        params.append(int(to_season))
        season_clauses.append("season<=?")
        season_params.append(int(to_season))

    with closing(repository._connect()) as connection:
        if season_clauses:
            connection.execute(f"""DELETE FROM cfb_play_value_metrics
              WHERE model_version=? AND play_id IN (
                SELECT play_id FROM cfb_plays WHERE {' AND '.join(season_clauses)}
              )""", (model_version, *season_params))
        else:
            connection.execute("DELETE FROM cfb_play_value_metrics WHERE model_version=?", (model_version,))
        connection.commit()

    now = datetime.now(timezone.utc).isoformat()
    scored_count = 0
    batch: list[tuple[Any, ...]] = []
    current_drive: tuple[int, str] | None = None
    drive_rows: list[Any] = []

    def flush_batch() -> None:
        nonlocal batch
        if not batch:
            return
        with closing(repository._connect()) as writer:
            writer.executemany(
                "INSERT OR REPLACE INTO cfb_play_value_metrics VALUES(?,?,?,?,?,?)", batch)
            writer.commit()
        batch = []

    def score_drive(rows: list[Any]) -> None:
        nonlocal scored_count, batch
        for index, row in enumerate(rows):
            before = _lookup(table, state_key(row))
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            after = _lookup(table, state_key(next_row)) if next_row is not None else 0.0
            realized = _realized_points(row)
            edpa = (realized + after - before) if before is not None and after is not None else None
            batch.append((row["play_id"], model_version, before, after, edpa, now))
            scored_count += 1
            if len(batch) >= WRITE_BATCH:
                flush_batch()

    with closing(repository._connect()) as reader:
        cursor = reader.execute(f"""
          SELECT p.play_id,p.game_id,p.drive_id,p.drive_number,p.play_number,p.down,p.distance,
                 p.yards_to_goal,p.scoring,p.play_type,m.rush_pass
          FROM cfb_plays p JOIN cfb_play_metrics m USING(play_id)
          WHERE {' AND '.join(clauses)} AND p.drive_id IS NOT NULL
          ORDER BY p.game_id,p.drive_number,p.play_number
        """, params)
        for row in cursor:
            key = (int(row["game_id"]), str(row["drive_id"]))
            if current_drive is not None and key != current_drive:
                score_drive(drive_rows)
                drive_rows = []
            current_drive = key
            drive_rows.append(row)
        if drive_rows:
            score_drive(drive_rows)
    flush_batch()
    return {"model_version": model_version, "scored": scored_count}
