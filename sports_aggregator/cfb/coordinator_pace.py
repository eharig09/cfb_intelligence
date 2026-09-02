"""How fast a coordinator's offences play, across every stop he has held.

`pace.season_pace_summary` answers this for one team in one season by walking
each game and bucketing plays into overlapping score states. That is the right
shape for a game report and the wrong one for a career: the buckets overlap, so
totals cannot be summed across them, and a coordinator with eight stops would
mean eight passes over the play table.

This aggregates the same measurement across every stop the coordinator held. The
tempo figure it produces is identical to the established one -- checked against
Ohio State's 2025 season, 32.01 seconds per play from both -- because it
reproduces the same rule: the gap to the previous snap *on the same drive*, so
the opponent's possession never counts as part of an offence's tempo, and gaps
over a minute are dropped as period, timeout or review artifacts.
"""

from __future__ import annotations

from typing import Any, Sequence

from sports_aggregator.cfb.pace import PACE_VERSION


#: Matches `pace`: the metrics table is versioned and both must read the same one.
METRIC_VERSION = "pbp-v1"

#: How many seasons back "recent" reaches. Long enough to cover a coordinator's
#: current stop, short enough that a scheme he abandoned does not dominate it.
RECENT_SEASONS = 3

#: Seconds per play from fewer intervals than this is noise, not tempo. A single
#: game contributes about twenty-five.
MIN_INTERVALS = 40

#: Game-clock seconds remaining, counted down across regulation so that a drive
#: crossing the end of a quarter still yields a sane gap.
_REMAINING = """CASE WHEN p.period <= 4
        THEN (4 - p.period) * 900 + p.clock_minutes * 60 + p.clock_seconds
        ELSE p.clock_minutes * 60 + p.clock_seconds END"""

_PACE_SQL = f"""
WITH ordered AS (
  SELECT p.game_id, p.season, m.rush_pass,
         {_REMAINING} AS remaining,
         LAG({_REMAINING}) OVER (
             PARTITION BY p.game_id, p.drive_id ORDER BY p.play_number
         ) AS previous_remaining
  FROM cfb_plays p
  JOIN cfb_play_metrics m USING(play_id)
  WHERE m.metric_version = ?
    AND COALESCE(m.garbage_time, 0) = 0
    AND m.rush_pass IN ('rush', 'pass')
    AND p.period IS NOT NULL
    AND p.clock_minutes IS NOT NULL AND p.clock_seconds IS NOT NULL
    AND p.season = ? AND p.{{column}} = ?
)
SELECT COUNT(*) AS plays,
       SUM(rush_pass = 'pass') AS passes,
       COUNT(DISTINCT game_id) AS games,
       SUM(CASE WHEN previous_remaining - remaining BETWEEN 1 AND 60
                THEN previous_remaining - remaining END) AS interval_seconds,
       SUM(CASE WHEN previous_remaining - remaining BETWEEN 1 AND 60
                THEN 1 END) AS intervals
FROM ordered
"""


def _finish(row: dict[str, Any]) -> dict[str, Any] | None:
    plays = int(row.get("plays") or 0)
    if not plays:
        return None
    games = int(row.get("games") or 0)
    intervals = int(row.get("intervals") or 0)
    seconds = float(row.get("interval_seconds") or 0)
    # Reported only when there is enough of it to mean anything; the play and
    # pass counts stand on their own either way.
    tempo = seconds / intervals if intervals >= MIN_INTERVALS else None
    return {
        "plays": plays,
        "passes": int(row.get("passes") or 0),
        "rushes": plays - int(row.get("passes") or 0),
        "games": games,
        "seasons": int(row.get("seasons") or 0),
        "pass_rate": int(row.get("passes") or 0) / plays,
        "plays_per_game": plays / games if games else None,
        "seconds_per_play": tempo,
        "plays_per_minute": (60.0 / tempo) if tempo else None,
        "intervals": intervals,
        "pace_version": PACE_VERSION,
    }


def _aggregate(repository, stops: Sequence[tuple[int, str]], *,
               column: str = "offense",
               metric_version: str = METRIC_VERSION) -> dict[str, Any] | None:
    """Sum the same measurement over every (season, team) the coordinator held.

    One query per stop rather than one query with the stops OR-ed together:
    `idx_cfb_plays_offense` covers (season, offense) exactly, and a single stop
    reads through it in 26ms, but OR-ing four of them puts the planner off the
    index entirely and the same four seasons take 7.2 seconds. Four seeks beat
    one scan.
    """
    from sports_aggregator.cfb.play_by_play import initialize

    stops = [(int(season), str(team)) for season, team in stops if team]
    if not stops:
        return None
    initialize(repository)
    statement = _PACE_SQL.format(column=column)
    totals = {"plays": 0, "passes": 0, "games": 0,
              "interval_seconds": 0.0, "intervals": 0}
    played = set()
    with repository._reader() as connection:
        for season, team in stops:
            row = connection.execute(statement, (metric_version, season, team)).fetchone()
            if row is None or not row["plays"]:
                continue
            played.add(season)
            for key in totals:
                totals[key] += (row[key] or 0)
    # Each stop is one season and a game belongs to one of them, so the counts
    # add without double counting.
    totals["seasons"] = len(played)
    return _finish(totals)


def stops_for(repository, coach_name: str, *, side: str = "offense",
              through_season: int | None = None) -> list[dict[str, Any]]:
    """Every season this coordinator held that job, newest first."""
    from sports_aggregator.cfb.coordinators import initialize

    initialize(repository)
    clause = "" if through_season is None else " AND season <= ?"
    params: list[Any] = [str(coach_name), str(side)]
    if through_season is not None:
        params.append(int(through_season))
    with repository._reader() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT season, team, team_id, role FROM coordinator_seasons "
            f"WHERE coach_name = ? AND side = ?{clause} ORDER BY season DESC",
            params)]


def coordinator_pace(repository, coach_name: str, *, side: str = "offense",
                     through_season: int | None = None,
                     recent_seasons: int = RECENT_SEASONS) -> dict[str, Any] | None:
    """Career and recent tempo for one coordinator, plus the stops behind it."""
    if not coach_name:
        return None
    stops = stops_for(repository, coach_name, side=side, through_season=through_season)
    if not stops:
        return None
    column = "offense" if side == "offense" else "defense"
    pairs = [(row["season"], row["team"]) for row in stops]
    newest = max(row["season"] for row in stops)
    recent = [pair for pair in pairs if pair[0] > newest - recent_seasons]
    return {
        "coach_name": coach_name,
        "side": side,
        "stops": stops,
        "career": _aggregate(repository, pairs, column=column),
        "recent": _aggregate(repository, recent, column=column),
        "recent_from": newest - recent_seasons + 1,
    }


def team_pace(repository, team: str, seasons: Sequence[int]) -> dict[str, Any] | None:
    """The same measurement for a team, for when the coordinator is unknown.

    Which is the usual case: `coordinator_seasons` is populated by a separate
    command that no refresh profile runs, so on most databases there is no name
    to attribute an offence to. The tempo is a property of the offence either
    way, and saying whose it is honestly beats showing nothing.
    """
    return _aggregate(repository, [(season, team) for season in seasons])
