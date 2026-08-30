"""Observed in-season depth-chart evidence from completed games.

Published/preseason depth charts are expectations. Once games are played, the
application should be able to distinguish that expectation from what a team is
actually doing. This module derives a conservative role signal from stored
player box scores while keeping the current-season roster as the eligibility
boundary.

Box scores are strong evidence for quarterbacks, ball carriers, receivers,
kickers and many defenders, but they are not snap charts. Players such as
linemen can participate heavily without recording an individual box-score row.
Those positions therefore remain on the projected ordering until a future snap
or starter source supplies stronger evidence.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any


# Usage-like events get most of the weight. Yardage and splash production are
# deliberately small secondary signals: this is trying to infer role, not rank
# who played the best game.
STAT_WEIGHTS: dict[tuple[str, str], float] = {
    ("passing", "ATT"): 1.00,
    ("passing", "CMP"): 0.20,
    ("passing", "COMPLETIONS"): 0.20,
    ("passing", "YDS"): 0.015,
    ("rushing", "CAR"): 1.00,
    ("rushing", "ATT"): 1.00,
    ("rushing", "YDS"): 0.020,
    ("receiving", "REC"): 1.35,
    ("receiving", "YDS"): 0.020,
    ("defensive", "TOT"): 0.90,
    ("defensive", "SOLO"): 0.25,
    ("defensive", "TFL"): 1.25,
    ("defensive", "SACKS"): 1.75,
    ("defensive", "QB HUR"): 0.50,
    ("defensive", "PD"): 0.75,
    ("interceptions", "INT"): 1.50,
    ("kicking", "FGA"): 1.00,
    ("kicking", "FGM"): 0.50,
    ("kicking", "XPA"): 0.50,
    ("kicking", "XPM"): 0.25,
    ("punting", "NO"): 1.00,
    ("kickReturns", "NO"): 1.00,
    ("puntReturns", "NO"): 1.00,
}

RECENCY_WEIGHTS = (1.00, 0.78, 0.62, 0.50)


def _weighted_value(category: str | None, stat_type: str | None,
                    numeric_value: Any) -> float:
    key = (str(category or ""), str(stat_type or "").strip().upper())
    weight = STAT_WEIGHTS.get(key)
    if weight is None or numeric_value is None:
        return 0.0
    try:
        value = max(0.0, float(numeric_value))
    except (TypeError, ValueError):
        return 0.0
    return value * weight


def observed_depth_roles(repository, team: str, season: int,
                         *, recent_games: int = 4) -> dict[str, dict[str, Any]]:
    """Return current-roster player role evidence from recent completed games.

    The current roster is joined first, which prevents a transferred-out or
    otherwise departed player from influencing the live depth board. Historical
    box-score rows remain useful only when the same player is rostered on this
    team for ``season``.
    """
    if not team:
        return {}
    repository.initialize()
    with closing(repository._connect()) as connection:
        roster = [dict(row) for row in connection.execute(
            """SELECT player_id,first_name,last_name,position
               FROM players WHERE season=? AND team=?""",
            (int(season), str(team)),
        ).fetchall()]
        player_ids = [str(row["player_id"]) for row in roster if row.get("player_id")]
        if not player_ids:
            return {}
        placeholders = ",".join("?" for _ in player_ids)
        rows = [dict(row) for row in connection.execute(
            f"""SELECT gp.player_id,gp.category,gp.stat_type,gp.numeric_value,
                       g.game_id,g.week,g.start_date
                FROM game_player_box_stats gp
                JOIN games g USING(game_id)
                WHERE g.season=? AND g.completed=1 AND gp.team=?
                  AND gp.player_id IN ({placeholders})
                ORDER BY g.start_date DESC,g.game_id DESC""",
            [int(season), str(team), *player_ids],
        ).fetchall()]

    if not rows:
        return {}

    # Identify the team's most recent games represented in the player box data.
    ordered_games: list[int] = []
    game_meta: dict[int, dict[str, Any]] = {}
    for row in rows:
        game_id = int(row["game_id"])
        if game_id not in game_meta:
            game_meta[game_id] = {
                "week": row.get("week"),
                "start_date": row.get("start_date"),
            }
            ordered_games.append(game_id)
    selected = ordered_games[: max(1, int(recent_games))]
    recency = {
        game_id: RECENCY_WEIGHTS[index] if index < len(RECENCY_WEIGHTS) else RECENCY_WEIGHTS[-1]
        for index, game_id in enumerate(selected)
    }

    per_player_game: dict[tuple[str, int], float] = {}
    for row in rows:
        game_id = int(row["game_id"])
        if game_id not in recency:
            continue
        player_id = str(row["player_id"])
        contribution = _weighted_value(
            row.get("category"), row.get("stat_type"), row.get("numeric_value")
        )
        if contribution <= 0:
            continue
        key = (player_id, game_id)
        per_player_game[key] = per_player_game.get(key, 0.0) + contribution

    roster_by_id = {str(row["player_id"]): row for row in roster}
    output: dict[str, dict[str, Any]] = {}
    for player_id, player in roster_by_id.items():
        appearances = [
            (game_id, score)
            for (pid, game_id), score in per_player_game.items()
            if pid == player_id and score > 0
        ]
        if not appearances:
            continue
        appearances.sort(key=lambda item: selected.index(item[0]) if item[0] in selected else 999)
        score = sum(value * recency.get(game_id, 0.0) for game_id, value in appearances)
        games = len(appearances)
        if games >= 3:
            confidence = "high"
        elif games >= 2:
            confidence = "medium"
        else:
            confidence = "early"
        latest_game = appearances[0][0]
        output[player_id] = {
            "player_id": player_id,
            "position": player.get("position"),
            "observed_score": round(score, 3),
            "observed_games": games,
            "confidence": confidence,
            "latest_week": game_meta.get(latest_game, {}).get("week"),
            "latest_game_id": latest_game,
            # Two games is enough to let observed usage reorder a projected
            # group. One-game evidence is displayed but does not overreact to a
            # single opener, garbage-time appearance or injury replacement.
            "can_reorder": games >= 2,
        }
    return output
