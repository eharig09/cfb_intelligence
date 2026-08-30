"""Context-aware turning points reconstructed from valid wp-v2 game states.

The persisted leverage column is useful as a cheap generic diagnostic, but it was
originally calculated between adjacent provider rows.  Provider PBP includes
administrative rows (end of period, no-play records, timeouts, etc.), so adjacent
raw rows are not always adjacent football states.  The postgame report therefore
reconstructs transitions from valid regulation states and computes a signed
before->after WP change at read time.

This keeps the fitted WP model untouched while making turning-point semantics
football-correct and auditable.
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
    """Report score: true valid-state WP swing adjusted for game context."""
    leverage = abs(float(row.get("wp_change") or 0.0))
    period = int(row.get("period") or 0)
    wp = row.get("home_wp_before")
    try:
        wp = float(wp)
    except (TypeError, ValueError):
        wp = 0.5

    time_weight = {1: 0.82, 2: 0.94, 3: 1.08, 4: 1.25}.get(period, 1.0)
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
    ranked = sorted(candidates, key=lambda r: float(r.get("turning_point_score") or 0.0), reverse=True)
    chosen: list[dict[str, Any]] = []
    period_counts: dict[int, int] = {}
    used: set[str] = set()

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
    """Return report-ready turning points using only valid regulation states."""
    from sports_aggregator.cfb.win_probability import initialize

    initialize(repository)
    game_id = int(game_id)
    limit = max(1, int(limit))

    with closing(repository._connect()) as connection:
        game = connection.execute(
            "SELECT completed,home_points,away_points FROM games WHERE game_id=?", (game_id,)
        ).fetchone()
        rows = [dict(row) for row in connection.execute("""
          WITH valid_states AS (
            SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                   p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,
                   p.down,p.distance,p.yardline,p.yards_to_goal,p.yards_gained,p.scoring,
                   p.drive_number,p.play_number,m.rush_pass,m.down_type,
                   w.home_win_probability,
                   LEAD(w.home_win_probability) OVER (
                     ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,
                              COALESCE(p.drive_number,0),COALESCE(p.play_number,0),p.play_id
                   ) AS next_home_win_probability
            FROM cfb_plays p
            JOIN cfb_play_win_probability w USING(play_id)
            LEFT JOIN cfb_play_metrics m
              ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
            WHERE p.game_id=? AND w.model_version=?
              AND p.period BETWEEN 1 AND 4
              AND p.down BETWEEN 1 AND 4
              AND p.distance IS NOT NULL
              AND p.yards_to_goal BETWEEN 1 AND 100
              AND p.offense IS NOT NULL AND TRIM(p.offense)<>''
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%no play%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%end of quarter%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%end of 1st%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%end of 2nd%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%end of 3rd%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%end of 4th%'
              AND LOWER(COALESCE(p.play_text,'')) NOT LIKE '%timeout%'
          )
          SELECT * FROM valid_states
          ORDER BY period,clock_minutes DESC,clock_seconds DESC,
                   COALESCE(drive_number,0),COALESCE(play_number,0),play_id
        """, (game_id, model_version)).fetchall()]

    if not rows:
        return []

    completed = bool(game and int(game["completed"] or 0))
    terminal_outcome: float | None = None
    if completed and game["home_points"] is not None and game["away_points"] is not None:
        terminal_outcome = 1.0 if float(game["home_points"]) > float(game["away_points"]) else 0.0

    candidates: list[dict[str, Any]] = []
    last_index = len(rows) - 1
    for index, row in enumerate(rows):
        before = row.get("home_win_probability")
        after = row.get("next_home_win_probability")
        if index == last_index and terminal_outcome is not None:
            after = terminal_outcome
            row["terminal_outcome"] = terminal_outcome
        if before is None or after is None:
            continue
        try:
            before_f = float(before)
            after_f = float(after)
        except (TypeError, ValueError):
            continue

        row["home_wp_before"] = before_f
        row["home_wp_after"] = after_f
        row["wp_change"] = after_f - before_f
        row["leverage"] = abs(row["wp_change"])
        row["wp_swing_points"] = 100.0 * abs(row["wp_change"])
        home_score, away_score = _home_score(row)
        row["home_score"] = home_score
        row["away_score"] = away_score
        row["turning_point_score"] = _ranking_score(row)
        candidates.append(row)

    return _select_diverse(candidates, limit)
