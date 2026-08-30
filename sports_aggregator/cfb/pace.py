"""Game-state-aware pace and play-selection analysis from normalized PBP.

Pace is intentionally its own versioned contract.  The raw plays do not change
when we refine the definition of a neutral situation, tempo denominator or
clock treatment; only this derived view does.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from typing import Any

PACE_VERSION = "pace-v1"


def _game_seconds_remaining(row: dict[str, Any]) -> int | None:
    period = row.get("period")
    minutes = row.get("clock_minutes")
    seconds = row.get("clock_seconds")
    if period is None or minutes is None or seconds is None:
        return None
    try:
        p = int(period); clock = int(minutes) * 60 + int(seconds)
    except (TypeError, ValueError):
        return None
    if p <= 4:
        return max(0, (4 - p) * 900 + clock)
    return max(0, clock)


def _state(row: dict[str, Any]) -> str:
    offense = int(row.get("offense_score") or 0)
    defense = int(row.get("defense_score") or 0)
    margin = offense - defense
    if abs(margin) <= 8:
        return "neutral"
    return "leading" if margin > 0 else "trailing"


def _detail_state(row: dict[str, Any]) -> str:
    offense = int(row.get("offense_score") or 0)
    defense = int(row.get("defense_score") or 0)
    margin = offense - defense
    if margin == 0:
        return "tied"
    if 0 < margin <= 8:
        return "leading_one_score"
    if margin > 8:
        return "leading_multi_score"
    if -8 <= margin < 0:
        return "trailing_one_score"
    return "trailing_multi_score"


def _rate_packet(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualifying = [r for r in rows if r.get("rush_pass") in {"rush", "pass"}]
    passes = sum(1 for r in qualifying if r["rush_pass"] == "pass")
    rushes = len(qualifying) - passes
    clocks = [(_game_seconds_remaining(r), r) for r in qualifying]
    clocks = [(value, row) for value, row in clocks if value is not None]
    # Play rate uses elapsed game-clock span represented by the state's snaps.
    # It is a descriptive tempo proxy, not seconds-to-snap; true snap interval
    # requires continuous clock/runoff metadata the historical feed may not have.
    span_minutes = None
    if len(clocks) >= 2:
        values = [value for value, _ in clocks]
        span_seconds = max(values) - min(values)
        if span_seconds > 0:
            span_minutes = span_seconds / 60.0
    return {
        "plays": len(qualifying),
        "passes": passes,
        "rushes": rushes,
        "pass_rate": passes / len(qualifying) if qualifying else None,
        "play_rate": len(qualifying) / span_minutes if span_minutes and span_minutes > 0 else None,
        "clock_span_minutes": span_minutes,
    }


def game_pace_summary(repository, game_id: int, *, metric_version: str = "pbp-v1") -> dict[str, Any]:
    """Return state-specific tempo and pass tendency for both offenses."""
    from sports_aggregator.cfb.play_by_play import initialize
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute("""
          SELECT p.offense,p.period,p.clock_minutes,p.clock_seconds,
                 p.offense_score,p.defense_score,m.rush_pass,m.garbage_time,m.down_type
          FROM cfb_plays p JOIN cfb_play_metrics m USING(play_id)
          WHERE p.game_id=? AND m.metric_version=?
          ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC
        """, (int(game_id), metric_version)).fetchall()]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("garbage_time") or row.get("rush_pass") not in {"rush", "pass"}:
            continue
        grouped[str(row["offense"])].append(row)

    teams: dict[str, Any] = {}
    for team, items in grouped.items():
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            buckets["overall"].append(row)
            buckets[_state(row)].append(row)
            buckets[_detail_state(row)].append(row)
            if int(row.get("period") or 0) <= 2:
                buckets["first_half"].append(row)
            else:
                buckets["second_half"].append(row)
            if row.get("down_type"):
                buckets[f"{row['down_type']}_downs"].append(row)
        teams[team] = {key: _rate_packet(value) for key, value in buckets.items()}
    return {
        "game_id": int(game_id),
        "pace_version": PACE_VERSION,
        "metric_version": metric_version,
        "neutral_definition": "score margin within 8 points; garbage time excluded",
        "play_rate_definition": "qualifying rush/pass snaps divided by represented game-clock span",
        "teams": teams,
    }


def season_pace_summary(repository, team: str, season: int, *, metric_version: str = "pbp-v1") -> dict[str, Any]:
    """Aggregate state tendencies across a team's stored season games."""
    from sports_aggregator.cfb.play_by_play import initialize
    initialize(repository)
    with closing(repository._connect()) as connection:
        game_ids = [int(row[0]) for row in connection.execute(
            "SELECT DISTINCT game_id FROM cfb_plays WHERE season=? AND offense=?",
            (int(season), str(team))).fetchall()]
    aggregate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game_id in game_ids:
        packet = game_pace_summary(repository, game_id, metric_version=metric_version)
        for key, row in (packet.get("teams", {}).get(str(team), {}) or {}).items():
            aggregate[key].append(row)

    result = {}
    for key, packets in aggregate.items():
        plays = sum(int(row.get("plays") or 0) for row in packets)
        passes = sum(int(row.get("passes") or 0) for row in packets)
        rushes = sum(int(row.get("rushes") or 0) for row in packets)
        weighted_play_rate_num = sum((float(row["play_rate"]) * int(row.get("plays") or 0))
                                     for row in packets if row.get("play_rate") is not None)
        weighted_play_rate_den = sum(int(row.get("plays") or 0) for row in packets
                                     if row.get("play_rate") is not None)
        result[key] = {
            "plays": plays, "passes": passes, "rushes": rushes,
            "pass_rate": passes / plays if plays else None,
            "play_rate": weighted_play_rate_num / weighted_play_rate_den if weighted_play_rate_den else None,
            "games": len(packets),
        }
    return {"team": str(team), "season": int(season), "pace_version": PACE_VERSION, "states": result}
