"""The middle of the field, run and pass together.

Two sources, because the provider publishes them differently. Pass direction
arrives per attempt on `/passing/plays` and is stored in `cfbd_passing_plays`;
rush direction is recovered from play text by the `play_detail` parser into
`cfb_play_detail`. Both key on the same play id, so our own ep-v2 EPA attaches
to either.

Coverage differs sharply by season and, within a season, by game. Rush direction
appears on 0.01-0.16% of 2022-2024 rushes but 41.75% of 2025 -- and per game that
is bimodal, 534 games at zero against 399 above 70%, rather than thinly spread.
So every figure here is returned with the play count behind it, and a caller
that cannot show the count should not show the number.

Deliberately offense-only per game. In one game a team's "middle allowed" is the
opponent's "middle thrown" -- the same plays counted twice -- so a game view that
prints both sides for both teams is showing two numbers four times. Season
figures are different: there the defence faced other opponents, so
`team_season_middle` does return both.
"""

from __future__ import annotations

from typing import Any

from sports_aggregator.cfb.repository import CFBRepository


MODEL_VERSION = "ep-v2"

#: Under this a split is one or two plays and moves on any of them.
MIN_GAME_PLAYS = 4
MIN_SEASON_PLAYS = 25

_RUN_SQL = """
  SELECT d.rush_direction AS direction, COUNT(*) AS plays,
         SUM(p.yards_gained) AS total_yards,
         SUM(e.epa) AS total_epa,
         SUM(COALESCE(m.success, 0)) AS successes,
         SUM(CASE WHEN e.epa IS NOT NULL THEN 1 ELSE 0 END) AS scored
  FROM cfb_plays p
  JOIN cfb_play_detail d ON d.play_id = p.play_id
  LEFT JOIN cfb_play_epa e ON e.play_id = p.play_id AND e.model_version = ?
  LEFT JOIN cfb_play_metrics m ON m.play_id = p.play_id
  WHERE d.rush_direction IS NOT NULL AND COALESCE(m.garbage_time, 0) = 0 AND {scope}
  GROUP BY d.rush_direction
"""

_PASS_SQL = """
  SELECT pp.pass_direction AS direction, COUNT(*) AS plays,
         SUM(pp.total_yards) AS total_yards,
         SUM(e.epa) AS total_epa,
         SUM(COALESCE(m.success, 0)) AS successes,
         SUM(CASE WHEN e.epa IS NOT NULL THEN 1 ELSE 0 END) AS scored
  FROM cfbd_passing_plays pp
  LEFT JOIN cfb_play_epa e ON e.play_id = pp.play_id AND e.model_version = ?
  LEFT JOIN cfb_play_metrics m ON m.play_id = pp.play_id
  WHERE pp.pass_direction IS NOT NULL AND COALESCE(m.garbage_time, 0) = 0 AND {scope}
  GROUP BY pp.pass_direction
"""


def _blank() -> dict[str, Any]:
    return {"plays": 0, "scored": 0, "total_epa": 0.0, "successes": 0,
            "total_yards": 0.0}


def _accumulate(rows, into: dict[str, dict[str, Any]]) -> None:
    for row in rows:
        bucket = into["middle" if row["direction"] == "middle" else "outside"]
        bucket["plays"] += row["plays"] or 0
        bucket["scored"] += row["scored"] or 0
        bucket["total_epa"] += row["total_epa"] or 0.0
        bucket["successes"] += row["successes"] or 0
        bucket["total_yards"] += row["total_yards"] or 0.0


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    """Both halves of the question: what was produced, and at what rate.

    Totals answer "how much came from here" and rates answer "how good was it".
    A team can lead the middle in yards because it ran there forty times and
    still be worse per play than one that went there nine times.
    """
    plays, scored = bucket["plays"], bucket["scored"]
    return {"plays": plays,
            "yards": bucket["total_yards"],
            "yards_per_play": (bucket["total_yards"] / plays) if plays else None,
            "epa_per_play": (bucket["total_epa"] / scored) if scored else None,
            "total_epa": bucket["total_epa"] if scored else None,
            "success_rate": (bucket["successes"] / plays) if plays else None,
            "scored": scored}


def _pair(connection, model_version: str, run_scope: str, pass_scope: str,
          run_params: tuple, pass_params: tuple) -> dict[str, Any]:
    run = {"middle": _blank(), "outside": _blank()}
    passing = {"middle": _blank(), "outside": _blank()}
    try:
        _accumulate(connection.execute(_RUN_SQL.format(scope=run_scope),
                                       (model_version, *run_params)), run)
    except Exception:
        pass
    try:
        _accumulate(connection.execute(_PASS_SQL.format(scope=pass_scope),
                                       (model_version, *pass_params)), passing)
    except Exception:
        pass
    combined = {"middle": _blank(), "outside": _blank()}
    for side in ("middle", "outside"):
        for source in (run, passing):
            for key in ("plays", "scored", "total_epa", "successes", "total_yards"):
                combined[side][key] += source[side][key]
    result = {name: {side: _finish(buckets[side]) for side in ("middle", "outside")}
              for name, buckets in (("run", run), ("pass", passing), ("combined", combined))}
    total = result["combined"]["middle"]["plays"] + result["combined"]["outside"]["plays"]
    result["plays"] = total
    result["middle_share"] = (result["combined"]["middle"]["plays"] / total) if total else None
    return result


def game_middle(repository: CFBRepository, game_id: int, *,
                model_version: str = MODEL_VERSION) -> dict[str, dict[str, Any]]:
    """Each team's own offence in one game. The defence is the other team's row."""
    from sports_aggregator.cfb.passing_plays import initialize as initialize_passing
    from sports_aggregator.cfb.play_detail import initialize as initialize_detail

    initialize_passing(repository)
    initialize_detail(repository)
    with repository._reader() as connection:
        # Both sources, because they are filled by different steps: syncing
        # passing detail without backfilling plays leaves a team with direction
        # data and no row in cfb_plays, and it should still be reported.
        teams = [row[0] for row in connection.execute(
            """SELECT DISTINCT offense FROM cfb_plays
               WHERE game_id=? AND offense IS NOT NULL
               UNION
               SELECT DISTINCT offense FROM cfbd_passing_plays
               WHERE game_id=? AND offense IS NOT NULL""",
            (int(game_id), int(game_id)))]
        return {team: _pair(
            connection, model_version,
            "p.game_id = ? AND p.offense = ?", "pp.game_id = ? AND pp.offense = ?",
            (int(game_id), team), (int(game_id), team)) for team in teams}


def team_season_middle(repository: CFBRepository, team: str, season: int, *,
                       model_version: str = MODEL_VERSION) -> dict[str, Any]:
    """Season to date: what a team does in the middle, and what it gives up there."""
    from sports_aggregator.cfb.passing_plays import initialize as initialize_passing
    from sports_aggregator.cfb.play_detail import initialize as initialize_detail

    initialize_passing(repository)
    initialize_detail(repository)
    with repository._reader() as connection:
        offense = _pair(
            connection, model_version,
            "p.season = ? AND p.offense = ?", "pp.season = ? AND pp.offense = ?",
            (int(season), str(team)), (int(season), str(team)))
        defense = _pair(
            connection, model_version,
            "p.season = ? AND p.defense = ?", "pp.season = ? AND pp.defense = ?",
            (int(season), str(team)), (int(season), str(team)))
    return {"team": team, "season": int(season), "offense": offense, "defense": defense}


def middle_verdict(teams: dict[str, dict[str, Any]], *,
                   minimum: int = MIN_GAME_PLAYS) -> dict[str, Any] | None:
    """Who won the middle, by combined run-and-pass EPA per play.

    None when either side is too thin to compare, which is the honest answer far
    more often than the alternative.
    """
    usable = {team: data for team, data in teams.items()
              if (data["combined"]["middle"]["plays"] or 0) >= minimum
              and data["combined"]["middle"]["epa_per_play"] is not None}
    if len(usable) != 2:
        return None
    (team_a, a), (team_b, b) = usable.items()
    value_a = a["combined"]["middle"]["epa_per_play"]
    value_b = b["combined"]["middle"]["epa_per_play"]
    if value_a == value_b:
        return None
    winner, loser = ((team_a, team_b) if value_a > value_b else (team_b, team_a))
    return {"winner": winner, "loser": loser,
            "winner_epa": max(value_a, value_b), "loser_epa": min(value_a, value_b),
            "margin": abs(value_a - value_b)}
