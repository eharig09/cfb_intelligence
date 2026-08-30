"""Terminal-aware turning points for the production wp-v2 model.

Persisted leverage is the absolute change from one pre-play WP state to the next.
The final play has no next pre-play state, so its terminal leverage is computed
against the actual completed-game outcome (1.0 home win, 0.0 home loss) when the
turning-point report is read. This avoids rewriting the full historical WP table.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any


def game_turning_points(repository, game_id: int, *, model_version: str = "wp-v2",
                        limit: int = 6) -> list[dict[str, Any]]:
    from sports_aggregator.cfb.win_probability import initialize

    initialize(repository)
    game_id = int(game_id)
    limit = max(1, int(limit))
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                 p.play_type,p.play_text,p.offense_score,p.defense_score,
                 w.home_win_probability,w.leverage
          FROM cfb_plays p
          JOIN cfb_play_win_probability w USING(play_id)
          WHERE p.game_id=? AND w.model_version=? AND w.leverage IS NOT NULL
          ORDER BY w.leverage DESC
          LIMIT ?
        """, (game_id, model_version, limit + 1)).fetchall()]

        terminal = connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                 p.play_type,p.play_text,p.offense_score,p.defense_score,
                 w.home_win_probability,g.completed,g.home_points,g.away_points
          FROM cfb_plays p
          JOIN cfb_play_win_probability w USING(play_id)
          JOIN games g USING(game_id)
          WHERE p.game_id=? AND w.model_version=?
          ORDER BY p.period DESC,p.clock_minutes ASC,p.clock_seconds ASC,
                   p.drive_number DESC,p.play_number DESC
          LIMIT 1
        """, (game_id, model_version)).fetchone()

    by_id = {str(row["play_id"]): row for row in rows}
    if terminal is not None and int(terminal["completed"] or 0):
        home_points = terminal["home_points"]
        away_points = terminal["away_points"]
        probability = terminal["home_win_probability"]
        if home_points is not None and away_points is not None and probability is not None:
            outcome = 1.0 if float(home_points) > float(away_points) else 0.0
            candidate = dict(terminal)
            candidate["leverage"] = abs(outcome - float(probability))
            candidate["terminal_outcome"] = outcome
            by_id[str(candidate["play_id"])] = candidate

    output = list(by_id.values())
    output.sort(key=lambda row: float(row.get("leverage") or 0.0), reverse=True)
    return output[:limit]
