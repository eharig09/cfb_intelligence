"""Event-centric turning points reconstructed from valid wp-v2 game states.

Provider PBP mixes real football events with administrative rows, and some major
scoring/return events do not carry a normal down/distance state. Turning points
therefore use valid pre-play WP states for the before/after probabilities, but
attribute each transition to the most meaningful football event that occurred
between those states.

The report suppresses implausible ordinary-play swings and applies independent
direction checks from ep-v1, actual score changes, and third/fourth-down results.
Stored wp-v2 predictions remain untouched for diagnostics.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any


ADMIN_WORDS = (
    "timeout", "end of quarter", "end of 1st", "end of 2nd", "end of 3rd",
    "end of 4th", "end of half", "end of game", "end of regulation", "no play",
)


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


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()


def _administrative(row: dict[str, Any]) -> bool:
    text = _text(row)
    return any(word in text for word in ADMIN_WORDS)


def _valid_state(row: dict[str, Any]) -> bool:
    if _administrative(row):
        return False
    try:
        period = int(row.get("period") or 0)
        down = int(row.get("down") or 0)
        distance = row.get("distance")
        ytg = int(row.get("yards_to_goal"))
    except (TypeError, ValueError):
        return False
    return (
        1 <= period <= 4 and 1 <= down <= 4 and distance is not None and
        1 <= ytg <= 100 and bool(str(row.get("offense") or "").strip()) and
        row.get("home_win_probability") is not None
    )


def _event_priority(row: dict[str, Any]) -> int:
    """Higher means this row should own a state transition when present."""
    if _administrative(row):
        return -1
    text = _text(row)
    scoring = int(row.get("scoring") or 0)
    if "touchdown" in text:
        return 100
    if "intercept" in text and ("return" in text or scoring):
        return 98
    if "fumble" in text and ("return" in text or "recovered" in text or scoring):
        return 96
    if "safety" in text:
        return 94
    if "field goal" in text:
        return 90
    if "intercept" in text or "fumble" in text:
        return 88
    if scoring:
        return 85
    if int(row.get("down") or 0) == 4:
        return 25
    return 0


def _choose_event(segment: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the football event responsible for a valid-state transition."""
    if not segment:
        return {}
    best = segment[0]
    best_priority = _event_priority(best)
    for row in segment[1:]:
        priority = _event_priority(row)
        if priority > best_priority:
            best = row
            best_priority = priority
        elif priority == best_priority and priority >= 85:
            best = row
    return best


def _ranking_score(row: dict[str, Any]) -> float:
    """Largest credible WP swings first; context only resolves near-ties."""
    leverage = abs(float(row.get("wp_change") or 0.0))
    period = int(row.get("period") or 0)
    before = row.get("home_wp_before")
    try:
        before = float(before)
    except (TypeError, ValueError):
        before = 0.5

    stage = {1: 0.98, 2: 1.00, 3: 1.03, 4: 1.08}.get(period, 1.0)
    closeness = max(0.0, 1.0 - 2.0 * abs(before - 0.5))
    competitive = 0.97 + 0.06 * closeness
    priority = int(row.get("event_priority") or 0)
    event = 1.08 if priority >= 85 else (1.03 if priority >= 25 else 1.0)
    return leverage * stage * competitive * event


def _directionally_consistent(row: dict[str, Any]) -> bool:
    """Require a WP move to agree with ep-v1's offense-perspective play value."""
    epa = row.get("epa")
    if epa is None:
        return True
    try:
        epa_f = float(epa)
        wp_change = float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError):
        return True
    if abs(epa_f) < 0.05 or abs(wp_change) < 0.005:
        return True
    offense_is_home = str(row.get("offense") or "") == str(row.get("home_team") or "")
    expected_home_sign = epa_f if offense_is_home else -epa_f
    return expected_home_sign * wp_change > 0


def _score_change_consistent(row: dict[str, Any]) -> bool:
    """Scoring-event WP direction must agree with the actual scoreboard change."""
    if int(row.get("event_priority") or 0) < 85:
        return True
    before_home = row.get("home_score")
    before_away = row.get("away_score")
    after_home = row.get("home_score_after")
    after_away = row.get("away_score_after")
    if None in (before_home, before_away, after_home, after_away):
        return True
    try:
        home_delta = int(after_home) - int(before_home)
        away_delta = int(after_away) - int(before_away)
        wp_change = float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError):
        return True
    if home_delta == away_delta:
        return True
    if home_delta > away_delta:
        return wp_change > 0
    return wp_change < 0


def _late_down_result_consistent(row: dict[str, Any]) -> bool:
    """A clear 3rd/4th-down success/failure must move WP toward the beneficiary."""
    if int(row.get("event_priority") or 0) >= 85:
        return True
    try:
        down = int(row.get("down") or 0)
        distance = int(row.get("distance") or 0)
        gained = int(row.get("yards_gained") or 0)
        wp_change = float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError):
        return True
    if down not in (3, 4) or distance <= 0 or abs(wp_change) < 0.005:
        return True
    offense_is_home = str(row.get("offense") or "") == str(row.get("home_team") or "")
    offense_succeeded = gained >= distance
    expected_home_positive = offense_succeeded if offense_is_home else not offense_succeeded
    return wp_change > 0 if expected_home_positive else wp_change < 0


def _credible_ordinary_swing(row: dict[str, Any]) -> bool:
    """Reject state discontinuities that are not believable football events."""
    if not _score_change_consistent(row):
        return False
    if not _late_down_result_consistent(row):
        return False
    if not _directionally_consistent(row):
        return False

    priority = int(row.get("event_priority") or 0)
    if priority >= 25:
        return True

    leverage = abs(float(row.get("wp_change") or 0.0))
    if leverage <= 0.12:
        return True

    try:
        period = int(row.get("period") or 0)
        minute = int(row.get("clock_minutes") or 0)
        second = int(row.get("clock_seconds") or 0)
        down = int(row.get("down") or 0)
        ytg = int(row.get("yards_to_goal") or 100)
        home_score = int(row.get("home_score") or 0)
        away_score = int(row.get("away_score") or 0)
    except (TypeError, ValueError):
        return False

    if period <= 3:
        return False

    remaining = minute * 60 + second
    score_margin = abs(home_score - away_score)
    if remaining <= 300 and score_margin <= 8 and (down >= 3 or ytg <= 10):
        return True
    return leverage <= 0.18


def game_turning_points(repository, game_id: int, *, model_version: str = "wp-v2",
                        limit: int = 6) -> list[dict[str, Any]]:
    """Return the largest event-attributed, report-credible WP changes."""
    from sports_aggregator.cfb.win_probability import initialize
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_ep

    initialize(repository)
    initialize_ep(repository)
    game_id = int(game_id)
    limit = max(1, int(limit))

    with closing(repository._connect()) as connection:
        game = connection.execute(
            "SELECT completed,home_points,away_points FROM games WHERE game_id=?", (game_id,)
        ).fetchone()
        rows = [dict(row) for row in connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,
                 p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,
                 p.down,p.distance,p.yardline,p.yards_to_goal,p.yards_gained,p.scoring,
                 p.drive_number,p.play_number,m.rush_pass,m.down_type,
                 w.home_win_probability,e.epa
          FROM cfb_plays p
          JOIN cfb_play_win_probability w USING(play_id)
          LEFT JOIN cfb_play_metrics m
            ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
          LEFT JOIN cfb_play_epa e
            ON e.play_id=p.play_id AND e.model_version='ep-v1'
          WHERE p.game_id=? AND w.model_version=? AND p.period BETWEEN 1 AND 4
          ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,
                   COALESCE(p.drive_number,0),COALESCE(p.play_number,0),p.play_id
        """, (game_id, model_version)).fetchall()]

    if not rows:
        return []

    terminal_outcome: float | None = None
    if game and int(game["completed"] or 0) and game["home_points"] is not None and game["away_points"] is not None:
        terminal_outcome = 1.0 if float(game["home_points"]) > float(game["away_points"]) else 0.0

    state_indices = [i for i, row in enumerate(rows) if _valid_state(row)]
    if not state_indices:
        return []

    candidates: list[dict[str, Any]] = []
    for position, start_index in enumerate(state_indices):
        state = rows[start_index]
        next_index = state_indices[position + 1] if position + 1 < len(state_indices) else None
        before = state.get("home_win_probability")
        if next_index is not None:
            next_state = rows[next_index]
            after = next_state.get("home_win_probability")
            segment = rows[start_index:next_index]
        else:
            next_state = None
            if terminal_outcome is None:
                continue
            after = terminal_outcome
            segment = rows[start_index:]

        if before is None or after is None:
            continue
        try:
            before_f = float(before)
            after_f = float(after)
        except (TypeError, ValueError):
            continue

        event = dict(_choose_event(segment) or state)
        for key in (
            "period", "clock_minutes", "clock_seconds", "offense", "defense",
            "home_team", "away_team", "offense_score", "defense_score", "down",
            "distance", "yardline", "yards_to_goal", "drive_number", "play_number",
        ):
            event[f"event_{key}"] = event.get(key)
            event[key] = state.get(key)

        if event.get("epa") is None:
            event["epa"] = state.get("epa")

        event["home_wp_before"] = before_f
        event["home_wp_after"] = after_f
        event["wp_change"] = after_f - before_f
        event["leverage"] = abs(event["wp_change"])
        event["wp_swing_points"] = 100.0 * event["leverage"]
        event["event_priority"] = _event_priority(event)
        event["attribution"] = "special_event" if event.get("play_id") != state.get("play_id") else "state_play"
        home_score, away_score = _home_score(state)
        event["home_score"] = home_score
        event["away_score"] = away_score
        if next_state is not None:
            home_after, away_after = _home_score(next_state)
            event["home_score_after"] = home_after
            event["away_score_after"] = away_after
        else:
            event["home_score_after"] = game["home_points"] if game else None
            event["away_score_after"] = game["away_points"] if game else None
        if next_index is None and terminal_outcome is not None:
            event["terminal_outcome"] = terminal_outcome
        event["turning_point_score"] = _ranking_score(event)
        if _credible_ordinary_swing(event):
            candidates.append(event)

    by_event: dict[str, dict[str, Any]] = {}
    for row in candidates:
        key = str(row.get("play_id") or "")
        existing = by_event.get(key)
        if existing is None or float(row.get("leverage") or 0) > float(existing.get("leverage") or 0):
            by_event[key] = row

    ranked = sorted(
        by_event.values(),
        key=lambda row: (float(row.get("turning_point_score") or 0.0), float(row.get("leverage") or 0.0)),
        reverse=True,
    )
    return ranked[:limit]
