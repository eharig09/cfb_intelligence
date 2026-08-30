"""Possession-aware expected points and EPA (ep-v1 / epa-v1).

EP-v1 estimates the offense-perspective value of the next scoring event before
halftime/end of regulation from down, distance, field position and time left in
the half. Targets are derived from scoreboard changes between consecutive
pre-play states rather than play-text scoring heuristics, so defensive scores,
safeties and multi-point scoring events are naturally signed against the team
that had possession.

EPA is then computed as:

    immediate net points + signed EP(next state) - EP(current state)

When possession changes, the next offense's EP is negated. At halftime/end of
regulation the continuation value is zero. Overtime rows are retained only as
transition context so the final regulation score can be observed; OT states are
never fitted or scored by ep-v1.

All fitting/scoring paths keep only one game in Python memory at a time.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import math
from typing import Any

MODEL_VERSION = "ep-v1"
WRITE_BATCH = 1000
MIN_CELL = 30


def initialize(repository) -> None:
    from sports_aggregator.cfb.play_by_play import initialize as initialize_pbp
    initialize_pbp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_expected_points_state (
          model_version TEXT NOT NULL,
          down_bucket INTEGER NOT NULL,
          distance_bucket INTEGER NOT NULL,
          field_bucket INTEGER NOT NULL,
          time_bucket INTEGER NOT NULL,
          samples INTEGER NOT NULL,
          expected_points REAL NOT NULL,
          fitted_at TEXT NOT NULL,
          PRIMARY KEY(model_version,down_bucket,distance_bucket,field_bucket,time_bucket)
        );
        CREATE TABLE IF NOT EXISTS cfb_play_epa (
          play_id TEXT NOT NULL,
          model_version TEXT NOT NULL,
          ep_before REAL,
          ep_after REAL,
          immediate_net_points REAL,
          possession_changed INTEGER,
          epa REAL,
          scored_at TEXT NOT NULL,
          PRIMARY KEY(play_id,model_version),
          FOREIGN KEY(play_id) REFERENCES cfb_plays(play_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_play_epa_model
          ON cfb_play_epa(model_version,play_id);
        """)
        connection.commit()


def _int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _down(value: Any) -> int:
    down = _int(value)
    return int(down) if down is not None and 1 <= down <= 4 else 0


def _distance(value: Any) -> int:
    distance = _int(value)
    if distance is None or distance < 0:
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
    ytg = _int(value)
    if ytg is None:
        return 0
    ytg = max(1, min(100, ytg))
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


def _half_remaining(row: Any) -> int | None:
    period = _int(row["period"])
    minutes = _int(row["clock_minutes"])
    seconds = _int(row["clock_seconds"])
    if period is None or period < 1 or period > 4 or minutes is None or seconds is None:
        return None
    clock = max(0, minutes * 60 + seconds)
    return clock + 900 if period in (1, 3) else clock


def _time(row: Any) -> int:
    remain = _half_remaining(row)
    if remain is None:
        return 0
    if remain <= 120:
        return 1
    if remain <= 300:
        return 2
    if remain <= 600:
        return 3
    if remain <= 900:
        return 4
    return 5


def state_key(row: Any) -> tuple[int, int, int, int]:
    return _down(row["down"]), _distance(row["distance"]), _field(row["yards_to_goal"]), _time(row)


def _is_home_offense(row: Any) -> bool | None:
    offense = str(row["offense"] or "")
    home = str(row["home_team"] or "")
    away = str(row["away_team"] or "")
    if offense and home and offense == home:
        return True
    if offense and away and offense == away:
        return False
    return None


def _home_away_scores(row: Any) -> tuple[int, int] | None:
    home_offense = _is_home_offense(row)
    offense_score = _int(row["offense_score"])
    defense_score = _int(row["defense_score"])
    if home_offense is None or offense_score is None or defense_score is None:
        return None
    return (offense_score, defense_score) if home_offense else (defense_score, offense_score)


def _same_half(a: Any, b: Any) -> bool:
    pa = _int(a["period"])
    pb = _int(b["period"])
    if pa is None or pb is None or not (1 <= pa <= 4 and 1 <= pb <= 4):
        return False
    return (pa <= 2 and pb <= 2) or (pa >= 3 and pb >= 3)


def _period_segment(row: Any) -> int:
    period = _int(row["period"]) or 0
    if 1 <= period <= 2:
        return 1
    if 3 <= period <= 4:
        return 2
    return 3


def _immediate_home_net(row: Any, next_row: Any | None,
                        final_home: int | None, final_away: int | None) -> float | None:
    before = _home_away_scores(row)
    if before is None:
        return None
    if next_row is not None:
        after = _home_away_scores(next_row)
    elif final_home is not None and final_away is not None:
        after = (int(final_home), int(final_away))
    else:
        after = None
    if after is None:
        return None
    dh = after[0] - before[0]
    da = after[1] - before[1]
    # Score corrections or malformed ordering should not be treated as football scoring events.
    if dh < 0 or da < 0:
        return None
    return float(dh - da)


def _training_rows(repository, from_season: int | None, to_season: int | None) -> tuple[str, list[Any]]:
    clauses = ["g.completed=1", "g.home_points IS NOT NULL", "g.away_points IS NOT NULL"]
    params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
    sql = f"""
      SELECT p.play_id,p.game_id,p.drive_number,p.play_number,p.offense,p.defense,
             p.home_team,p.away_team,p.offense_score,p.defense_score,p.period,
             p.clock_minutes,p.clock_seconds,p.down,p.distance,p.yards_to_goal,
             p.provider_ppa,g.home_points,g.away_points
      FROM cfb_plays p JOIN games g USING(game_id)
      WHERE {' AND '.join(clauses)}
      ORDER BY p.game_id,p.period,p.clock_minutes DESC,p.clock_seconds DESC,p.drive_number,p.play_number
    """
    return sql, params


def _iter_games(repository, from_season: int | None, to_season: int | None):
    sql, params = _training_rows(repository, from_season, to_season)
    with closing(repository._connect()) as connection:
        current_game: int | None = None
        rows: list[dict[str, Any]] = []
        for row in connection.execute(sql, params):
            game_id = int(row["game_id"])
            if current_game is not None and game_id != current_game:
                yield rows
                rows = []
            rows.append(dict(row))
            current_game = game_id
        if rows:
            yield rows


def _targets_for_game(rows: list[dict[str, Any]]) -> list[float | None]:
    targets: list[float | None] = [None] * len(rows)
    next_event_home: float | None = None
    next_segment: int | None = None
    final_home = _int(rows[-1].get("home_points")) if rows else None
    final_away = _int(rows[-1].get("away_points")) if rows else None

    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        segment = _period_segment(row)
        if next_segment is not None and segment != next_segment:
            next_event_home = None
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        # Always use the next scoreboard state for immediate points, even across halftime/OT.
        # Continuation value is reset separately by segment boundaries.
        immediate = _immediate_home_net(
            row, next_row,
            final_home if next_row is None else None,
            final_away if next_row is None else None,
        )
        if immediate is not None and abs(immediate) > 1e-9:
            next_event_home = immediate
        home_offense = _is_home_offense(row)
        if home_offense is not None and segment in (1, 2):
            targets[index] = 0.0 if next_event_home is None else (
                next_event_home if home_offense else -next_event_home
            )
        next_segment = segment
    return targets


def fit_model(repository, *, from_season: int | None = None,
              to_season: int | None = None, model_version: str = MODEL_VERSION,
              min_cell: int = MIN_CELL) -> dict[str, Any]:
    initialize(repository)
    cells: dict[tuple[int, int, int, int], list[float]] = {}
    global_sum = 0.0
    global_count = 0
    games = 0
    negative_targets = 0
    scoring_targets = 0

    for rows in _iter_games(repository, from_season, to_season):
        games += 1
        targets = _targets_for_game(rows)
        for row, target in zip(rows, targets):
            key = state_key(row)
            if target is None or 0 in key:
                continue
            aggregate = cells.setdefault(key, [0.0, 0.0])
            aggregate[0] += float(target)
            aggregate[1] += 1
            global_sum += float(target)
            global_count += 1
            scoring_targets += int(abs(float(target)) > 1e-9)
            negative_targets += int(float(target) < 0)

    global_mean = global_sum / global_count if global_count else 0.0
    now = datetime.now(timezone.utc).isoformat()
    output = []
    for key, (cell_sum, raw_count) in cells.items():
        n = int(raw_count)
        raw = cell_sum / n
        weight = n / (n + max(1, int(min_cell)))
        estimate = weight * raw + (1.0 - weight) * global_mean
        output.append((model_version, *key, n, estimate, now))

    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfb_expected_points_state WHERE model_version=?", (model_version,))
        connection.executemany("""INSERT INTO cfb_expected_points_state
          (model_version,down_bucket,distance_bucket,field_bucket,time_bucket,samples,expected_points,fitted_at)
          VALUES(?,?,?,?,?,?,?,?)""", output)
        connection.commit()
    return {
        "model_version": model_version,
        "plays": global_count,
        "games": games,
        "cells": len(output),
        "global_expected_points": round(global_mean, 4),
        "scoring_target_share": round(scoring_targets / global_count, 4) if global_count else 0.0,
        "negative_target_share": round(negative_targets / global_count, 4) if global_count else 0.0,
        "from_season": from_season,
        "to_season": to_season,
    }


def _load_model(repository, model_version: str):
    with closing(repository._connect()) as connection:
        rows = connection.execute("""SELECT down_bucket,distance_bucket,field_bucket,time_bucket,
          expected_points,samples FROM cfb_expected_points_state WHERE model_version=?""",
          (model_version,)).fetchall()
    return {
        (int(r[0]), int(r[1]), int(r[2]), int(r[3])): (float(r[4]), int(r[5]))
        for r in rows
    }


def _weighted(candidates: list[tuple[float, int]]) -> float | None:
    if not candidates:
        return None
    total = sum(n for _, n in candidates)
    return sum(value * n for value, n in candidates) / total if total else None


def _lookup(table, key: tuple[int, int, int, int]) -> float | None:
    if 0 in key:
        return None
    if key in table:
        return table[key][0]
    down, _dist, field, time = key
    value = _weighted([(v, n) for (d, _x, f, t), (v, n) in table.items()
                       if d == down and f == field and t == time])
    if value is not None:
        return value
    value = _weighted([(v, n) for (d, _x, f, _t), (v, n) in table.items()
                       if d == down and f == field])
    if value is not None:
        return value
    value = _weighted([(v, n) for (_d, _x, f, t), (v, n) in table.items()
                       if f == field and t == time])
    if value is not None:
        return value
    return _weighted([(v, n) for (_d, _x, f, _t), (v, n) in table.items() if f == field])


def score_plays(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    initialize(repository)
    table = _load_model(repository, model_version)
    if not table:
        return {"model_version": model_version, "scored": 0, "reason": "model_not_fitted"}

    season_clauses: list[str] = []
    season_params: list[Any] = []
    if from_season is not None:
        season_clauses.append("season>=?"); season_params.append(int(from_season))
    if to_season is not None:
        season_clauses.append("season<=?"); season_params.append(int(to_season))
    with closing(repository._connect()) as connection:
        if season_clauses:
            connection.execute(f"""DELETE FROM cfb_play_epa WHERE model_version=? AND play_id IN (
              SELECT play_id FROM cfb_plays WHERE {' AND '.join(season_clauses)}
            )""", (model_version, *season_params))
        else:
            connection.execute("DELETE FROM cfb_play_epa WHERE model_version=?", (model_version,))
        connection.commit()

    now = datetime.now(timezone.utc).isoformat()
    batch: list[tuple[Any, ...]] = []
    scored = 0
    possession_changes = 0
    scoring_plays = 0

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        with closing(repository._connect()) as writer:
            writer.executemany("""INSERT OR REPLACE INTO cfb_play_epa
              (play_id,model_version,ep_before,ep_after,immediate_net_points,possession_changed,epa,scored_at)
              VALUES(?,?,?,?,?,?,?,?)""", batch)
            writer.commit()
        batch = []

    for rows in _iter_games(repository, from_season, to_season):
        final_home = _int(rows[-1].get("home_points"))
        final_away = _int(rows[-1].get("away_points"))
        for index, row in enumerate(rows):
            before = _lookup(table, state_key(row))
            if before is None:
                continue
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            same_half = next_row is not None and _same_half(row, next_row)
            immediate_home = _immediate_home_net(
                row, next_row,
                final_home if next_row is None else None,
                final_away if next_row is None else None,
            )
            if immediate_home is None:
                immediate_home = 0.0
            home_offense = _is_home_offense(row)
            if home_offense is None:
                continue
            immediate = immediate_home if home_offense else -immediate_home
            scoring_plays += int(abs(immediate) > 1e-9)

            changed = False
            signed_after = 0.0
            if same_half and next_row is not None:
                next_ep = _lookup(table, state_key(next_row))
                if next_ep is not None:
                    changed = str(next_row.get("offense") or "") != str(row.get("offense") or "")
                    signed_after = -float(next_ep) if changed else float(next_ep)
            epa = float(immediate) + float(signed_after) - float(before)
            batch.append((row["play_id"], model_version, before, signed_after, immediate,
                          int(changed), epa, now))
            scored += 1
            possession_changes += int(changed)
            if len(batch) >= WRITE_BATCH:
                flush()
    flush()
    return {
        "model_version": model_version,
        "scored": scored,
        "possession_changes": possession_changes,
        "scoring_plays": scoring_plays,
    }


def validate_model(repository, *, from_season: int | None = None,
                   to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    """Validate EP against realized next-score targets; suitable for temporal holdouts."""
    initialize(repository)
    table = _load_model(repository, model_version)
    if not table:
        return {"model_version": model_version, "plays": 0, "reason": "model_not_fitted"}
    n = 0
    sum_pred = 0.0
    sum_target = 0.0
    abs_error = 0.0
    sq_error = 0.0
    by_field: dict[int, list[float]] = {}
    for rows in _iter_games(repository, from_season, to_season):
        targets = _targets_for_game(rows)
        for row, target in zip(rows, targets):
            if target is None:
                continue
            pred = _lookup(table, state_key(row))
            if pred is None:
                continue
            error = float(pred) - float(target)
            n += 1
            sum_pred += float(pred)
            sum_target += float(target)
            abs_error += abs(error)
            sq_error += error * error
            field = _field(row.get("yards_to_goal"))
            acc = by_field.setdefault(field, [0.0, 0.0, 0.0])
            acc[0] += float(pred); acc[1] += float(target); acc[2] += 1
    field_report = {}
    for field, (sp, st, count) in sorted(by_field.items()):
        if count:
            field_report[str(field)] = {
                "plays": int(count),
                "mean_prediction": round(sp / count, 4),
                "mean_target": round(st / count, 4),
                "bias": round((sp - st) / count, 4),
            }
    return {
        "model_version": model_version,
        "from_season": from_season,
        "to_season": to_season,
        "overall": {
            "plays": n,
            "mean_prediction": round(sum_pred / n, 4) if n else None,
            "mean_target": round(sum_target / n, 4) if n else None,
            "mae": round(abs_error / n, 5) if n else None,
            "rmse": round(math.sqrt(sq_error / n), 5) if n else None,
            "bias": round((sum_pred - sum_target) / n, 5) if n else None,
        },
        "by_field_bucket": field_report,
        "evaluation_note": (
            "Targets are offense-perspective next scoring event before halftime/end regulation; "
            "overtime states are excluded. Use a separately versioned temporal holdout model for unbiased evaluation."
        ),
    }
