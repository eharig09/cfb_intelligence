"""How a game moved by quarter and by half.

A final EPA margin says who was better; it does not say when. A team that was
level for three quarters and pulled away in the fourth and one that led wire to
wire land in the same place, and the two describe different teams.

Regulation only. Overtime is a different game with different rules -- possession
starts at the twenty-five and the clock is gone -- so folding it into "the fourth
quarter" would mean comparing plays the expected-points model was never fitted
on. Games that went to overtime still report their four regulation quarters.
"""

from __future__ import annotations

from typing import Any

from sports_aggregator.cfb.repository import CFBRepository


MODEL_VERSION = "ep-v2"

#: Under this a quarter is a handful of snaps and its rate is noise. Whole-game
#: splits are shown regardless, because the count is printed beside them.
MIN_PHASE_PLAYS = 8

#: Regulation quarters, and the halves they make up.
QUARTERS = (1, 2, 3, 4)
HALVES = {"first": (1, 2), "second": (3, 4)}

_SQL = """
  SELECT p.offense AS team, p.period AS period,
         COUNT(*) AS plays,
         SUM(p.yards_gained) AS total_yards,
         SUM(e.epa) AS total_epa,
         SUM(CASE WHEN e.epa IS NOT NULL THEN 1 ELSE 0 END) AS scored,
         SUM(COALESCE(m.success, 0)) AS successes
  FROM cfb_plays p
  LEFT JOIN cfb_play_epa e ON e.play_id = p.play_id AND e.model_version = ?
  LEFT JOIN cfb_play_metrics m ON m.play_id = p.play_id
  WHERE p.period BETWEEN 1 AND 4 AND m.rush_pass IN ('rush', 'pass')
    AND COALESCE(m.garbage_time, 0) = 0 AND {scope}
  GROUP BY p.offense, p.period
"""


def _blank() -> dict[str, Any]:
    return {"plays": 0, "scored": 0, "total_epa": 0.0, "total_yards": 0.0, "successes": 0}


def _add(into: dict[str, Any], row) -> None:
    into["plays"] += row["plays"] or 0
    into["scored"] += row["scored"] or 0
    into["total_epa"] += row["total_epa"] or 0.0
    into["total_yards"] += row["total_yards"] or 0.0
    into["successes"] += row["successes"] or 0


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    plays, scored = bucket["plays"], bucket["scored"]
    return {"plays": plays,
            "yards": bucket["total_yards"],
            "yards_per_play": (bucket["total_yards"] / plays) if plays else None,
            "total_epa": bucket["total_epa"] if scored else None,
            "epa_per_play": (bucket["total_epa"] / scored) if scored else None,
            "success_rate": (bucket["successes"] / plays) if plays else None}


def game_phases(repository: CFBRepository, game_id: int, *,
                model_version: str = MODEL_VERSION) -> dict[str, dict[str, Any]]:
    """Per team: each regulation quarter, each half, and the game."""
    # Joins cfb_play_epa as well as cfb_plays, so the module that owns it is
    # initialized too: a first read on a fresh database otherwise fails on a
    # table nobody has created yet.
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_epa
    from sports_aggregator.cfb.play_by_play import initialize as initialize_plays

    initialize_plays(repository)
    initialize_epa(repository)
    raw: dict[str, dict[int, dict[str, Any]]] = {}
    with repository._reader() as connection:
        for row in connection.execute(_SQL.format(scope="p.game_id = ?"),
                                      (model_version, int(game_id))):
            raw.setdefault(str(row["team"]), {})[int(row["period"])] = row

    teams: dict[str, dict[str, Any]] = {}
    for team, periods in raw.items():
        quarters: dict[int, dict[str, Any]] = {}
        for quarter in QUARTERS:
            bucket = _blank()
            if quarter in periods:
                _add(bucket, periods[quarter])
            quarters[quarter] = _finish(bucket)
        halves: dict[str, dict[str, Any]] = {}
        for name, members in HALVES.items():
            bucket = _blank()
            for quarter in members:
                if quarter in periods:
                    _add(bucket, periods[quarter])
            halves[name] = _finish(bucket)
        whole = _blank()
        for row in periods.values():
            _add(whole, row)
        teams[team] = {"quarters": quarters, "halves": halves, "game": _finish(whole)}
    return teams


def phase_margin(teams: dict[str, dict[str, Any]], scope: str, key: Any) -> dict[str, Any] | None:
    """Who was better in one phase, by EPA per play.

    `scope` is "quarters", "halves" or "game"; `key` selects within it. None when
    either side is too thin to compare, which a close game often is.
    """
    if len(teams) != 2:
        return None
    values = {}
    for team, data in teams.items():
        bucket = data["game"] if scope == "game" else data[scope].get(key)
        if not bucket or bucket["epa_per_play"] is None or bucket["plays"] < MIN_PHASE_PLAYS:
            return None
        values[team] = bucket["epa_per_play"]
    (team_a, a), (team_b, b) = values.items()
    if a == b:
        return None
    winner, loser = (team_a, team_b) if a > b else (team_b, team_a)
    return {"winner": winner, "loser": loser, "winner_epa": max(a, b),
            "loser_epa": min(a, b), "margin": abs(a - b)}


def team_season_phases(repository: CFBRepository, team: str, season: int, *,
                       model_version: str = MODEL_VERSION,
                       through_week: int | None = None) -> dict[str, Any]:
    """Season to date, by phase, offence and the defence it played."""
    # Joins cfb_play_epa as well as cfb_plays, so the module that owns it is
    # initialized too: a first read on a fresh database otherwise fails on a
    # table nobody has created yet.
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_epa
    from sports_aggregator.cfb.play_by_play import initialize as initialize_plays

    initialize_plays(repository)
    initialize_epa(repository)
    bounds = "" if through_week is None else " AND p.week <= ?"
    extra: tuple = () if through_week is None else (int(through_week),)
    sides: dict[str, dict[str, Any]] = {}
    with repository._reader() as connection:
        for side, column in (("offense", "p.offense"), ("defense", "p.defense")):
            periods: dict[int, dict[str, Any]] = {}
            for row in connection.execute(
                    _SQL.format(scope=f"p.season = ? AND {column} = ?{bounds}"),
                    (model_version, int(season), str(team), *extra)):
                # Grouped by offence in the SQL; for the defensive side every row
                # is an opponent, so periods are merged rather than keyed by team.
                bucket = periods.setdefault(int(row["period"]), _blank())
                _add(bucket, row)
            quarters = {q: _finish(periods.get(q) or _blank()) for q in QUARTERS}
            halves = {}
            for name, members in HALVES.items():
                bucket = _blank()
                for quarter in members:
                    if quarter in periods:
                        for key in bucket:
                            bucket[key] += periods[quarter][key]
                halves[name] = _finish(bucket)
            whole = _blank()
            for bucket in periods.values():
                for key in whole:
                    whole[key] += bucket[key]
            sides[side] = {"quarters": quarters, "halves": halves, "game": _finish(whole)}
    return {"team": team, "season": int(season), **sides}
