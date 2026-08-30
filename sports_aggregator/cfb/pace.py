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


def _margin(row: dict[str, Any]) -> int:
    return int(row.get("offense_score") or 0) - int(row.get("defense_score") or 0)


def _detail_state(row: dict[str, Any]) -> str:
    margin = _margin(row)
    if margin == 0:
        return "tied"
    if 0 < margin <= 8:
        return "leading_one_score"
    if margin > 8:
        return "leading_multi_score"
    if -8 <= margin < 0:
        return "trailing_one_score"
    return "trailing_multi_score"


def _attach_snap_intervals(items: list[dict[str, Any]]) -> None:
    """Attach game-clock seconds since the prior offensive snap on a drive.

    Staying within a drive avoids counting the opponent's possession as part of
    an offense's tempo. Intervals above 60 seconds are ignored as likely period,
    timeout, review or feed-boundary artifacts. A stopped game clock can yield
    zero and therefore contributes no denominator time.
    """
    previous: dict[str, tuple[int, dict[str, Any]]] = {}
    for row in items:
        row["snap_interval_seconds"] = None
        drive = str(row.get("drive_id") or "")
        current = _game_seconds_remaining(row)
        if not drive or current is None:
            continue
        prior = previous.get(drive)
        if prior is not None:
            prior_clock, _prior_row = prior
            interval = prior_clock - current
            if 0 < interval <= 60:
                row["snap_interval_seconds"] = interval
        previous[drive] = (current, row)


def _rate_packet(rows: list[dict[str, Any]]) -> dict[str, Any]:
    qualifying = [r for r in rows if r.get("rush_pass") in {"rush", "pass"}]
    passes = sum(1 for r in qualifying if r["rush_pass"] == "pass")
    rushes = len(qualifying) - passes
    intervals = [float(r["snap_interval_seconds"]) for r in qualifying
                 if r.get("snap_interval_seconds") is not None]
    minutes = sum(intervals) / 60.0 if intervals else None
    # Each valid interval represents one subsequent snap. Using intervals rather
    # than total state span prevents opponent possessions from diluting tempo.
    play_rate = len(intervals) / minutes if minutes and minutes > 0 else None
    seconds_per_play = sum(intervals) / len(intervals) if intervals else None
    return {
        "plays": len(qualifying),
        "passes": passes,
        "rushes": rushes,
        "pass_rate": passes / len(qualifying) if qualifying else None,
        "play_rate": play_rate,
        "seconds_per_play_clock": seconds_per_play,
        "tempo_intervals": len(intervals),
    }


def game_pace_summary(repository, game_id: int, *, metric_version: str = "pbp-v1") -> dict[str, Any]:
    """Return overlapping score-state tempo and pass tendency for both offenses."""
    from sports_aggregator.cfb.play_by_play import initialize
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute("""
          SELECT p.offense,p.drive_id,p.play_number,p.period,p.clock_minutes,p.clock_seconds,
                 p.offense_score,p.defense_score,m.rush_pass,m.garbage_time,m.down_type
          FROM cfb_plays p JOIN cfb_play_metrics m USING(play_id)
          WHERE p.game_id=? AND m.metric_version=?
          ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,p.drive_number,p.play_number
        """, (int(game_id), metric_version)).fetchall()]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("garbage_time") or row.get("rush_pass") not in {"rush", "pass"}:
            continue
        grouped[str(row["offense"])].append(row)

    teams: dict[str, Any] = {}
    for team, items in grouped.items():
        _attach_snap_intervals(items)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            margin = _margin(row)
            buckets["overall"].append(row)
            # These are deliberately overlapping lenses: a team leading 7-3 is
            # both leading and neutral. That lets game script and close-game
            # tendency be studied independently.
            if abs(margin) <= 8:
                buckets["neutral"].append(row)
            if margin > 0:
                buckets["leading"].append(row)
            elif margin < 0:
                buckets["trailing"].append(row)
            else:
                buckets["tied"].append(row)
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
        "neutral_definition": "absolute score margin <= 8; garbage time excluded",
        "leading_definition": "offense score margin > 0; overlaps neutral when lead <= 8",
        "trailing_definition": "offense score margin < 0; overlaps neutral when deficit <= 8",
        "play_rate_definition": "valid same-drive offensive snap intervals per game-clock minute",
        "seconds_per_play_definition": "mean game-clock seconds between qualifying same-drive offensive snaps",
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
        interval_count = sum(int(row.get("tempo_intervals") or 0) for row in packets)
        interval_seconds = sum(float(row.get("seconds_per_play_clock") or 0) * int(row.get("tempo_intervals") or 0)
                               for row in packets if row.get("seconds_per_play_clock") is not None)
        sec_per_play = interval_seconds / interval_count if interval_count else None
        result[key] = {
            "plays": plays, "passes": passes, "rushes": rushes,
            "pass_rate": passes / plays if plays else None,
            "seconds_per_play_clock": sec_per_play,
            "play_rate": 60.0 / sec_per_play if sec_per_play and sec_per_play > 0 else None,
            "tempo_intervals": interval_count,
            "games": len(packets),
        }
    return {"team": str(team), "season": int(season), "pace_version": PACE_VERSION, "states": result}
