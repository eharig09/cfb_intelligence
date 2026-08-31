"""Monotonic post-hoc calibration for stored win-probability models.

This layer deliberately leaves the underlying WP state model untouched. It
learns how empirical outcomes map from raw probability to calibrated probability
on a training window, stores that mapping under a separate version, and can then
score a temporal holdout without leakage.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from sports_aggregator.cfb.repository import schema_once

WRITE_BATCH = 1000
CALIBRATION_BINS = 40


@schema_once("wp_calibration")
def initialize(repository) -> None:
    from sports_aggregator.cfb.win_probability import initialize as initialize_wp
    initialize_wp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_wp_calibration_model (
          calibration_version TEXT NOT NULL,
          source_model_version TEXT NOT NULL,
          bin_index INTEGER NOT NULL,
          lower_bound REAL NOT NULL,
          upper_bound REAL NOT NULL,
          samples INTEGER NOT NULL,
          raw_mean REAL NOT NULL,
          calibrated_probability REAL NOT NULL,
          fitted_at TEXT NOT NULL,
          PRIMARY KEY(calibration_version,bin_index)
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_wp_calibration_source
          ON cfb_wp_calibration_model(source_model_version,calibration_version);
        """)
        connection.commit()


def _pava(values: list[tuple[float, int]]) -> list[float]:
    """Pool-adjacent-violators weighted isotonic regression."""
    blocks: list[dict[str, float]] = []
    for index, (value, weight) in enumerate(values):
        blocks.append({"start": index, "end": index, "sum": value * weight, "weight": float(weight)})
        while len(blocks) >= 2:
            a, b = blocks[-2], blocks[-1]
            mean_a = a["sum"] / a["weight"]
            mean_b = b["sum"] / b["weight"]
            if mean_a <= mean_b:
                break
            merged = {
                "start": a["start"], "end": b["end"],
                "sum": a["sum"] + b["sum"], "weight": a["weight"] + b["weight"],
            }
            blocks[-2:] = [merged]
    output = [0.5] * len(values)
    for block in blocks:
        mean = block["sum"] / block["weight"]
        for index in range(int(block["start"]), int(block["end"]) + 1):
            output[index] = min(.999, max(.001, mean))
    return output


def fit_calibration(repository, *, source_model_version: str = "wp-v1",
                    calibration_version: str = "wp-v1-calibrated",
                    from_season: int | None = None,
                    to_season: int | None = None,
                    bins: int = CALIBRATION_BINS) -> dict[str, Any]:
    initialize(repository)
    bins = max(10, min(100, int(bins)))
    sums_p = [0.0] * bins
    sums_y = [0.0] * bins
    counts = [0] * bins
    clauses = ["w.model_version=?", "w.home_win_probability IS NOT NULL",
               "g.completed=1", "g.home_points IS NOT NULL", "g.away_points IS NOT NULL"]
    params: list[Any] = [source_model_version]
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
    with closing(repository._connect()) as connection:
        cursor = connection.execute(f"""
          SELECT w.home_win_probability,g.home_points,g.away_points
          FROM cfb_play_win_probability w
          JOIN cfb_plays p USING(play_id)
          JOIN games g USING(game_id)
          WHERE {' AND '.join(clauses)}
        """, params)
        for row in cursor:
            probability = min(.999999, max(.000001, float(row["home_win_probability"])))
            outcome = 1.0 if float(row["home_points"]) > float(row["away_points"]) else 0.0
            index = min(bins - 1, int(probability * bins))
            counts[index] += 1
            sums_p[index] += probability
            sums_y[index] += outcome
    populated = [i for i, count in enumerate(counts) if count]
    if not populated:
        return {"calibration_version": calibration_version, "samples": 0,
                "reason": "source_predictions_missing"}
    observed = [(sums_y[i] / counts[i], counts[i]) for i in populated]
    monotonic = _pava(observed)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for position, index in enumerate(populated):
        rows.append((
            calibration_version, source_model_version, index,
            index / bins, (index + 1) / bins, counts[index],
            sums_p[index] / counts[index], monotonic[position], now,
        ))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfb_wp_calibration_model WHERE calibration_version=?",
                           (calibration_version,))
        connection.executemany("""INSERT INTO cfb_wp_calibration_model
          (calibration_version,source_model_version,bin_index,lower_bound,upper_bound,
           samples,raw_mean,calibrated_probability,fitted_at)
          VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        connection.commit()
    return {
        "calibration_version": calibration_version,
        "source_model_version": source_model_version,
        "samples": sum(counts),
        "bins": len(rows),
        "from_season": from_season,
        "to_season": to_season,
    }


def _lookup(rows: list[dict[str, Any]], probability: float) -> float:
    p = min(.999999, max(.000001, float(probability)))
    containing = next((row for row in rows if row["lower_bound"] <= p < row["upper_bound"]), None)
    if containing is not None:
        return float(containing["calibrated_probability"])
    nearest = min(rows, key=lambda row: abs(float(row["raw_mean"]) - p))
    return float(nearest["calibrated_probability"])


def score_calibrated(repository, *, source_model_version: str = "wp-v1",
                     calibration_version: str = "wp-v1-calibrated",
                     output_model_version: str | None = None,
                     from_season: int | None = None,
                     to_season: int | None = None) -> dict[str, Any]:
    initialize(repository)
    output_version = output_model_version or calibration_version
    with closing(repository._connect()) as connection:
        calibration = [dict(row) for row in connection.execute("""
          SELECT lower_bound,upper_bound,raw_mean,calibrated_probability
          FROM cfb_wp_calibration_model WHERE calibration_version=?
          ORDER BY bin_index
        """, (calibration_version,)).fetchall()]
    if not calibration:
        return {"model_version": output_version, "scored": 0, "reason": "calibrator_not_fitted"}

    clauses = ["w.model_version=?", "w.home_win_probability IS NOT NULL"]
    params: list[Any] = [source_model_version]
    season_clauses: list[str] = []
    season_params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
        season_clauses.append("season>=?"); season_params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
        season_clauses.append("season<=?"); season_params.append(int(to_season))

    with closing(repository._connect()) as connection:
        if season_clauses:
            connection.execute(f"""DELETE FROM cfb_play_win_probability
              WHERE model_version=? AND play_id IN (
                SELECT play_id FROM cfb_plays WHERE {' AND '.join(season_clauses)}
              )""", (output_version, *season_params))
        else:
            connection.execute("DELETE FROM cfb_play_win_probability WHERE model_version=?", (output_version,))
        connection.commit()

    now = datetime.now(timezone.utc).isoformat()
    batch: list[tuple[Any, ...]] = []
    pending: tuple[str, float] | None = None
    current_game: int | None = None
    scored = 0

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        with closing(repository._connect()) as writer:
            writer.executemany("INSERT OR REPLACE INTO cfb_play_win_probability VALUES(?,?,?,?,?)", batch)
            writer.commit()
        batch = []

    def emit(play_id: str, probability: float, next_probability: float | None) -> None:
        nonlocal scored
        leverage = abs(next_probability - probability) if next_probability is not None else None
        batch.append((play_id, output_version, probability, leverage, now))
        scored += 1
        if len(batch) >= WRITE_BATCH:
            flush()

    with closing(repository._connect()) as reader:
        cursor = reader.execute(f"""
          SELECT p.play_id,p.game_id,w.home_win_probability
          FROM cfb_play_win_probability w JOIN cfb_plays p USING(play_id)
          WHERE {' AND '.join(clauses)}
          ORDER BY p.game_id,p.period,p.clock_minutes DESC,p.clock_seconds DESC,p.drive_number,p.play_number
        """, params)
        for row in cursor:
            game_id = int(row["game_id"])
            probability = _lookup(calibration, float(row["home_win_probability"]))
            if current_game is not None and game_id != current_game:
                if pending is not None:
                    emit(pending[0], pending[1], None)
                pending = None
            if pending is not None:
                emit(pending[0], pending[1], probability)
            pending = (str(row["play_id"]), probability)
            current_game = game_id
        if pending is not None:
            emit(pending[0], pending[1], None)
    flush()
    return {
        "model_version": output_version,
        "source_model_version": source_model_version,
        "calibration_version": calibration_version,
        "scored": scored,
    }
