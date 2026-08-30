"""Empirical in-house expected-drive-points model.

This is deliberately *not* labeled EPA.  Version EDP-v1 estimates how many
points the offense will score on the current possession from down, distance and
field position.  It gives us an independently trained state-value model and a
play value (EDPA) without borrowing CFBD PPA.  A later possession-aware EP
model can incorporate opponent next-possession value and replace this cleanly.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

MODEL_VERSION = "edp-v1"
MIN_CELL = 20


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
    if distance <= 2: return 1
    if distance <= 5: return 2
    if distance <= 10: return 3
    if distance <= 15: return 4
    return 5


def _field(value: Any) -> int:
    try:
        ytg = max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return 0
    if ytg <= 10: return 1
    if ytg <= 20: return 2
    if ytg <= 40: return 3
    if ytg <= 60: return 4
    if ytg <= 80: return 5
    return 6


def state_key(row: dict[str, Any]) -> tuple[int, int, int]:
    return _down(row.get("down")), _distance(row.get("distance")), _field(row.get("yards_to_goal"))


def fit_model(repository, *, from_season: int | None = None,
              to_season: int | None = None, model_version: str = MODEL_VERSION,
              min_cell: int = MIN_CELL) -> dict[str, Any]:
    """Fit expected current-drive points from completed stored drives."""
    initialize(repository)
    where = ["p.drive_id IS NOT NULL", "d.points IS NOT NULL"]
    params: list[Any] = ["pbp-v1"]
    if from_season is not None:
        where.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        where.append("p.season<=?"); params.append(int(to_season))
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(f"""
          SELECT p.play_id,p.game_id,p.drive_id,p.down,p.distance,p.yards_to_goal,d.points
          FROM cfb_plays p
          JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version=?
          JOIN cfb_drive_metrics d ON d.game_id=p.game_id AND d.drive_id=p.drive_id
                               AND d.metric_version=m.metric_version
          WHERE {' AND '.join(where)} AND m.rush_pass IN ('rush','pass')
        """, params).fetchall()]

    cells: dict[tuple[int,int,int], list[float]] = defaultdict(list)
    global_values: list[float] = []
    for row in rows:
        key = state_key(row)
        if 0 in key:
            continue
        target = float(row.get("points") or 0)
        cells[key].append(target); global_values.append(target)
    global_mean = sum(global_values) / len(global_values) if global_values else 0.0
    fitted_at = datetime.now(timezone.utc).isoformat()
    output = []
    # Empirical Bayes-style shrinkage toward the global mean stabilizes sparse cells.
    for key, values in cells.items():
        n = len(values)
        raw = sum(values) / n
        weight = n / (n + max(1, int(min_cell)))
        estimate = weight * raw + (1.0 - weight) * global_mean
        output.append((*key, n, estimate))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfb_expected_drive_points WHERE model_version=?", (model_version,))
        connection.executemany("""INSERT INTO cfb_expected_drive_points
          (model_version,down_bucket,distance_bucket,field_bucket,samples,expected_points,fitted_at)
          VALUES(?,?,?,?,?,?,?)""",
          [(model_version,*row,fitted_at) for row in output])
        connection.commit()
    return {"model_version": model_version, "plays": len(rows), "cells": len(output),
            "global_mean": round(global_mean,4), "from_season": from_season, "to_season": to_season}


def _lookup(table: dict[tuple[int,int,int], tuple[float,int]], key: tuple[int,int,int]) -> float | None:
    if key in table:
        return table[key][0]
    # Back off first on distance, then down. Field position is retained because
    # it is the strongest possession-level state input.
    down, distance, field = key
    candidates = [(value, n) for (d, _dist, f), (value, n) in table.items() if d == down and f == field]
    if candidates:
        total = sum(n for _, n in candidates)
        return sum(value*n for value,n in candidates) / total
    candidates = [(value, n) for (_d, _dist, f), (value, n) in table.items() if f == field]
    if candidates:
        total = sum(n for _, n in candidates)
        return sum(value*n for value,n in candidates) / total
    return None


def score_plays(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    """Attach expected-drive-points-added values to stored plays."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        model_rows = connection.execute("""SELECT down_bucket,distance_bucket,field_bucket,
          expected_points,samples FROM cfb_expected_drive_points WHERE model_version=?""",
          (model_version,)).fetchall()
        table = {(int(r[0]),int(r[1]),int(r[2])):(float(r[3]),int(r[4])) for r in model_rows}
        clauses = ["m.metric_version='pbp-v1'", "m.rush_pass IN ('rush','pass')"]
        params: list[Any] = []
        if from_season is not None:
            clauses.append("p.season>=?"); params.append(int(from_season))
        if to_season is not None:
            clauses.append("p.season<=?"); params.append(int(to_season))
        plays = [dict(row) for row in connection.execute(f"""
          SELECT p.play_id,p.game_id,p.drive_id,p.drive_number,p.play_number,p.down,p.distance,
                 p.yards_to_goal,p.scoring,p.play_type
          FROM cfb_plays p JOIN cfb_play_metrics m USING(play_id)
          WHERE {' AND '.join(clauses)}
          ORDER BY p.game_id,p.drive_number,p.play_number
        """, params).fetchall()]
    if not table:
        return {"model_version": model_version, "scored": 0, "reason": "model_not_fitted"}

    by_drive: dict[tuple[int,str], list[dict[str,Any]]] = defaultdict(list)
    for row in plays:
        if row.get("drive_id"):
            by_drive[(int(row["game_id"]), str(row["drive_id"]))].append(row)
    now = datetime.now(timezone.utc).isoformat(); scored = []
    for group in by_drive.values():
        for index, row in enumerate(group):
            before = _lookup(table, state_key(row))
            next_row = group[index+1] if index+1 < len(group) else None
            after = _lookup(table, state_key(next_row)) if next_row else 0.0
            # Realized points are credited on terminal scoring plays; EDP is
            # current-drive value, so no opponent possession term is included.
            realized = 0.0
            if row.get("scoring"):
                text = str(row.get("play_type") or "").casefold()
                if "field goal" in text: realized = 3.0
                elif "two" in text: realized = 2.0
                elif "touchdown" in text: realized = 6.0
                elif "safety" in text: realized = 2.0
            edpa = (realized + after - before) if before is not None and after is not None else None
            scored.append((row["play_id"],model_version,before,after,edpa,now))
    with closing(repository._connect()) as connection:
        if scored:
            ids = [row[0] for row in scored]
            placeholders = ",".join("?" for _ in ids)
            connection.execute(f"DELETE FROM cfb_play_value_metrics WHERE model_version=? AND play_id IN ({placeholders})",
                               (model_version,*ids))
            connection.executemany("INSERT INTO cfb_play_value_metrics VALUES(?,?,?,?,?,?)", scored)
        connection.commit()
    return {"model_version": model_version, "scored": len(scored)}
