"""Streaming feature-based win probability model (wp-v2).

WP-v2 keeps the auditable, low-memory philosophy of wp-v1 but replaces coarse
state buckets with a logistic model over continuous game state plus pregame
strength.  It is intentionally dependency-free so it can be trained on the
memory-constrained Render host.

Features are all pre-play / pregame information:
- home score margin
- game time remaining
- score-margin x late-game interaction
- possession
- field position oriented to the home team
- down and distance
- pregame Elo differential
- neutral-site indicator

The model writes predictions into the same cfb_play_win_probability table used
by wp-v1, so the existing validation and turning-point tooling can compare model
versions directly.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
from typing import Any

MODEL_VERSION = "wp-v2"
WRITE_BATCH = 1000
FEATURE_VERSION = "wp-v2-features-1"


def initialize(repository) -> None:
    from sports_aggregator.cfb.win_probability import initialize as initialize_wp
    initialize_wp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_win_probability_logistic_model (
          model_version TEXT PRIMARY KEY,
          feature_version TEXT NOT NULL,
          coefficients_json TEXT NOT NULL,
          epochs INTEGER NOT NULL,
          learning_rate REAL NOT NULL,
          l2 REAL NOT NULL,
          samples INTEGER NOT NULL,
          fitted_at TEXT NOT NULL,
          from_season INTEGER,
          to_season INTEGER
        );
        """)
        connection.commit()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _home_state(row: Any) -> tuple[float, bool, float]:
    offense_is_home = str(row["offense"]) == str(row["home_team"])
    offense_score = _num(row["offense_score"])
    defense_score = _num(row["defense_score"])
    if offense_is_home:
        home_margin = offense_score - defense_score
    else:
        home_margin = defense_score - offense_score

    yards_to_goal = max(1.0, min(100.0, _num(row["yards_to_goal"], 50.0)))
    # Distance from the home team's attacking goal. Lower = better for home.
    home_ytg = yards_to_goal if offense_is_home else 100.0 - yards_to_goal
    return home_margin, offense_is_home, home_ytg


def _game_remaining(row: Any) -> float:
    try:
        period = int(row["period"])
        clock = int(row["clock_minutes"] or 0) * 60 + int(row["clock_seconds"] or 0)
    except (TypeError, ValueError):
        return 1800.0
    if period <= 4:
        return float(max(0, (4 - period) * 900 + clock))
    return 0.0


def features(row: Any) -> list[float]:
    margin, home_possession, home_ytg = _home_state(row)
    remaining = _game_remaining(row)
    elapsed = 1.0 - min(1.0, max(0.0, remaining / 3600.0))
    down = max(1.0, min(4.0, _num(row["down"], 1.0)))
    distance = max(0.0, min(25.0, _num(row["distance"], 10.0)))
    home_elo = _num(row["home_pregame_elo"], 1500.0)
    away_elo = _num(row["away_pregame_elo"], 1500.0)
    elo_diff = home_elo - away_elo
    neutral = 1.0 if int(row["neutral_site"] or 0) else 0.0

    # Scaled features keep SGD stable without requiring a materialized standardizer.
    margin_scaled = max(-3.0, min(3.0, margin / 21.0))
    field_scaled = (50.0 - home_ytg) / 50.0  # positive = home nearer scoring goal
    time_scaled = remaining / 3600.0
    possession = 1.0 if home_possession else -1.0
    return [
        1.0,                              # intercept
        margin_scaled,
        time_scaled,
        margin_scaled * elapsed,          # margin matters more late
        possession,
        field_scaled,
        (down - 2.5) / 1.5,
        (distance - 7.5) / 10.0,
        max(-3.0, min(3.0, elo_diff / 400.0)),
        neutral,
    ]


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 35.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -35.0))
    return z / (1.0 + z)


def _predict(weights: list[float], x: list[float]) -> float:
    return _sigmoid(sum(w * value for w, value in zip(weights, x)))


def _training_sql(from_season: int | None, to_season: int | None) -> tuple[str, list[Any]]:
    clauses = ["g.completed=1", "g.home_points IS NOT NULL", "g.away_points IS NOT NULL"]
    params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?")
        params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?")
        params.append(int(to_season))
    sql = f"""
      SELECT p.offense,p.home_team,p.offense_score,p.defense_score,p.period,
             p.clock_minutes,p.clock_seconds,p.yards_to_goal,p.down,p.distance,
             g.home_points,g.away_points,g.home_pregame_elo,g.away_pregame_elo,g.neutral_site
      FROM cfb_plays p JOIN games g USING(game_id)
      WHERE {' AND '.join(clauses)}
    """
    return sql, params


def fit_model(repository, *, from_season: int | None = None,
              to_season: int | None = None, model_version: str = MODEL_VERSION,
              epochs: int = 5, learning_rate: float = 0.025,
              l2: float = 0.0005) -> dict[str, Any]:
    """Fit dependency-free logistic WP with streaming SGD."""
    initialize(repository)
    epochs = max(1, int(epochs))
    learning_rate = float(learning_rate)
    l2 = max(0.0, float(l2))
    weights = [0.0] * 10
    sql, params = _training_sql(from_season, to_season)
    samples = 0
    final_log_loss = 0.0

    for epoch in range(epochs):
        epoch_samples = 0
        epoch_loss = 0.0
        # A gentle epoch decay keeps later passes from oscillating around the optimum.
        eta = learning_rate / math.sqrt(epoch + 1.0)
        with closing(repository._connect()) as connection:
            for row in connection.execute(sql, params):
                x = features(row)
                y = 1.0 if float(row["home_points"]) > float(row["away_points"]) else 0.0
                p = _predict(weights, x)
                error = y - p
                for index in range(len(weights)):
                    penalty = 0.0 if index == 0 else l2 * weights[index]
                    weights[index] += eta * (error * x[index] - penalty)
                clipped = min(.999999, max(.000001, p))
                epoch_loss += -(y * math.log(clipped) + (1.0 - y) * math.log(1.0 - clipped))
                epoch_samples += 1
        samples = epoch_samples
        final_log_loss = epoch_loss / epoch_samples if epoch_samples else 0.0

    fitted_at = datetime.now(timezone.utc).isoformat()
    with closing(repository._connect()) as connection:
        connection.execute("""INSERT OR REPLACE INTO cfb_win_probability_logistic_model
          (model_version,feature_version,coefficients_json,epochs,learning_rate,l2,samples,
           fitted_at,from_season,to_season) VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (model_version, FEATURE_VERSION, json.dumps(weights), epochs, learning_rate, l2,
           samples, fitted_at, from_season, to_season))
        connection.commit()
    return {
        "model_version": model_version,
        "feature_version": FEATURE_VERSION,
        "plays": samples,
        "epochs": epochs,
        "training_log_loss_last_epoch": round(final_log_loss, 5),
        "coefficients": [round(value, 6) for value in weights],
        "from_season": from_season,
        "to_season": to_season,
    }


def _load_weights(repository, model_version: str) -> list[float] | None:
    initialize(repository)
    with closing(repository._connect()) as connection:
        row = connection.execute(
            "SELECT coefficients_json FROM cfb_win_probability_logistic_model WHERE model_version=?",
            (model_version,)).fetchone()
    if not row:
        return None
    values = json.loads(str(row[0]))
    return [float(value) for value in values]


def score_plays(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str, Any]:
    """Score stored plays in streaming order and attach leverage to the causing play."""
    weights = _load_weights(repository, model_version)
    if not weights:
        return {"model_version": model_version, "scored": 0, "reason": "model_not_fitted"}

    clauses: list[str] = []
    params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?")
        params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?")
        params.append(int(to_season))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with closing(repository._connect()) as connection:
        delete_clauses = []
        delete_params: list[Any] = [model_version]
        if from_season is not None:
            delete_clauses.append("season>=?")
            delete_params.append(int(from_season))
        if to_season is not None:
            delete_clauses.append("season<=?")
            delete_params.append(int(to_season))
        if delete_clauses:
            connection.execute(f"""DELETE FROM cfb_play_win_probability
              WHERE model_version=? AND play_id IN (
                SELECT play_id FROM cfb_plays WHERE {' AND '.join(delete_clauses)}
              )""", delete_params)
        else:
            connection.execute("DELETE FROM cfb_play_win_probability WHERE model_version=?", (model_version,))
        connection.commit()

    now = datetime.now(timezone.utc).isoformat()
    batch: list[tuple[Any, ...]] = []
    scored = 0
    pending: tuple[str, float] | None = None
    current_game: int | None = None

    def flush() -> None:
        nonlocal batch
        if not batch:
            return
        with closing(repository._connect()) as writer:
            writer.executemany(
                "INSERT OR REPLACE INTO cfb_play_win_probability VALUES(?,?,?,?,?)", batch)
            writer.commit()
        batch = []

    def emit(play_id: str, probability: float, next_probability: float | None) -> None:
        nonlocal scored, batch
        leverage = abs(next_probability - probability) if next_probability is not None else None
        batch.append((play_id, model_version, probability, leverage, now))
        scored += 1
        if len(batch) >= WRITE_BATCH:
            flush()

    with closing(repository._connect()) as reader:
        cursor = reader.execute(f"""
          SELECT p.play_id,p.game_id,p.offense,p.home_team,p.offense_score,p.defense_score,
                 p.period,p.clock_minutes,p.clock_seconds,p.yards_to_goal,p.down,p.distance,
                 p.drive_number,p.play_number,g.home_pregame_elo,g.away_pregame_elo,g.neutral_site
          FROM cfb_plays p JOIN games g USING(game_id)
          {where}
          ORDER BY p.game_id,p.period,p.clock_minutes DESC,p.clock_seconds DESC,p.drive_number,p.play_number
        """, params)
        for row in cursor:
            game_id = int(row["game_id"])
            probability = _predict(weights, features(row))
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
    return {"model_version": model_version, "scored": scored}
