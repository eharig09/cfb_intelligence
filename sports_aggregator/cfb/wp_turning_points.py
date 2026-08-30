"""Context-aware turning points for the production wp-v2 model.

Raw persisted leverage remains the absolute pre-play WP change.  The report uses
an additional ranking score so routine early-game state changes do not crowd out
later, more consequential football moments.  The ranking score is presentation
logic only; it does not modify the underlying WP model or stored leverage.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any


def _home_score(row: dict[str, Any]) -> tuple[int | None, int | None]:
    offense_score = row.get("offense_score")
    defense_score = row.get("defense_score")
    if offense_score is None or defense_score is None:
        return None, None
    try:
        offense_score = int(offense_score)
        defense_score = int(defense_score)
    except (TypeError, ValueError):
        return None, None
    if str(row.get("offense") or "") == str(row.get("home_team") or ""):
        return offense_score, defense_score
    return defense_score, offense_score


def _is_routine_kick(row: dict[str, Any]) -> bool:
    text = f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    if any(word in text for word in ("intercept", "fumble", "touchdown", "field goal")):
        return False
    return any(word in text for word in ("kickoff", "extra point", "pat ", "pat,"))


def _ranking_score(row: dict[str, Any]) -> float:
    """Report score: leverage adjusted for timing, competitiveness and play meaning."""
    leverage = float(row.get("leverage") or 0.0)
    period = int(row.get("period") or 0)
    wp = row.get("home_win_probability")
    try:
        wp = float(wp)
    except (TypeError, ValueError):
        wp = 0.5

    # Later swings deserve modestly more report weight, while still allowing a
    # genuinely huge first-quarter event to rank.
    time_weight = {1: 0.82, 2: 0.94, 3: 1.08, 4: 1.25}.get(period, 1.12)
    # A swing near a competitive 50/50 state is more consequential than an
    # equal numerical wobble when the game is already almost decided.
    closeness = max(0.0, 1.0 - 2.0 * abs(wp - 0.5))
    contest_weight = 0.82 + 0.38 * closeness

    text = f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    meaning = 1.0
    if any(word in text for word in ("intercept", "fumble", "touchdown", "field goal")):
        meaning += 0.12
    if int(row.get("down") or 0) == 4:
        meaning += 0.08
    if int(row.get("scoring") or 0):
        meaning += 0.08
    if _is_routine_kick(row):
        meaning *= 0.42

    return leverage * time_weight * contest_weight * meaning


def _select_diverse(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Avoid one short early cluster monopolizing a game report."""
    ranked = sorted(candidates, key=lambda r: float(r.get("turning_point_score") or 0.0), reverse=True)
    chosen: list[dict[str, Any]] = []
    period_counts: dict[int, int] = {}
    used: set[str] = set()

    # First pass: at most two per period. This is deliberately soft; the fill
    # pass below can add more when a game truly has few meaningful periods.
    for row in ranked:
        period = int(row.get("period") or 0)
        if period_counts.get(period, 0) >= 2:
            continue
        play_id = str(row.get("play_id"))
        chosen.append(row)
        used.add(play_id)
        period_counts[period] = period_counts.get(period, 0) + 1
        if len(chosen) >= limit:
            return chosen

    for row in ranked:
        if str(row.get("play_id")) in used:
            continue
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def game_turning_points(repository, game_id: int, *, model_version: str = "wp-v2",
                        limit: int = 6) -> list[dict[str, Any]]:
    from sports_aggregator.cfb.win_probability import initialize

    initialize(repository)
    game_id = int(game_id)
    limit = max(1, int(limit))
    candidate_limit = max(36, limit * 8)
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                 p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,
                 p.down,p.distance,p.yardline,p.yards_to_goal,p.yards_gained,p.scoring,
                 p.drive_number,p.play_number,m.rush_pass,m.down_type,
                 w.home_win_probability,w.leverage
          FROM cfb_plays p
          JOIN cfb_play_win_probability w USING(play_id)
          LEFT JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
          WHERE p.game_id=? AND w.model_version=? AND w.leverage IS NOT NULL
          ORDER BY w.leverage DESC
          LIMIT ?
        """, (game_id, model_version, candidate_limit)).fetchall()]

        terminal = connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                 p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,
                 p.down,p.distance,p.yardline,p.yards_to_goal,p.yards_gained,p.scoring,
                 p.drive_number,p.play_number,m.rush_pass,m.down_type,
                 w.home_win_probability,g.completed,g.home_points,g.away_points
          FROM cfb_plays p
          JOIN cfb_play_win_probability w USING(play_id)
          LEFT JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
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

    candidates = list(by_id.values())
    for row in candidates:
        home_score, away_score = _home_score(row)
        row["home_score"] = home_score
        row["away_score"] = away_score
        row["turning_point_score"] = _ranking_score(row)
        probability = row.get("home_win_probability")
        leverage = float(row.get("leverage") or 0.0)
        if probability is not None:
            before = float(probability)
            row["home_wp_before"] = before
            # Direction cannot be recovered from absolute stored leverage for
            # ordinary plays, so expose the magnitude unless terminal outcome
            # gives us an exact after-state.
            row["home_wp_after"] = row.get("terminal_outcome")
            row["wp_swing_points"] = 100.0 * leverage

    return _select_diverse(candidates, limit)
