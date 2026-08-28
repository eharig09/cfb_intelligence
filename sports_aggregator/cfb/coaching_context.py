"""Team-page coaching context and historical coach lineage."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.tables import Column, Table


def _record(wins: int | None, losses: int | None, ties: int | None = 0) -> str:
    wins = int(wins or 0)
    losses = int(losses or 0)
    ties = int(ties or 0)
    return f"{wins}-{losses}" + (f"-{ties}" if ties else "")


def team_coaching_context(repository, team_id: int, season: int) -> dict[str, Any]:
    """Current Elo plus all-time head-coach context for one team."""
    repository.initialize()
    team = repository.get_team(int(team_id))
    if team is None:
        return {}

    with closing(repository._connect()) as connection:
        current = connection.execute(
            """SELECT * FROM coach_seasons
               WHERE team_id=? AND season<=?
               ORDER BY season DESC, games DESC
               LIMIT 1""",
            (int(team_id), int(season)),
        ).fetchone()

        coach = None
        if current is not None:
            current = dict(current)
            coach_id = int(current["coach_id"])
            coach_name = f"{current['first_name']} {current['last_name']}".strip()

            team_rows = connection.execute(
                """SELECT season,games,wins,losses,ties FROM coach_seasons
                   WHERE coach_id=? AND team_id=? AND season<=?
                   ORDER BY season DESC""",
                (coach_id, int(team_id), int(season)),
            ).fetchall()

            seasons = sorted({int(row["season"]) for row in team_rows}, reverse=True)
            latest = seasons[0] if seasons else int(current["season"])
            tenure_start = latest
            for candidate in seasons[1:]:
                if candidate == tenure_start - 1:
                    tenure_start = candidate
                else:
                    break
            tenure_years = latest - tenure_start + 1

            at_team = connection.execute(
                """SELECT COALESCE(SUM(wins),0) wins,COALESCE(SUM(losses),0) losses,
                          COALESCE(SUM(ties),0) ties
                   FROM coach_seasons WHERE coach_id=? AND team_id=?""",
                (coach_id, int(team_id)),
            ).fetchone()
            career = connection.execute(
                """SELECT COALESCE(SUM(wins),0) wins,COALESCE(SUM(losses),0) losses,
                          COALESCE(SUM(ties),0) ties
                   FROM coach_seasons WHERE coach_id=?""",
                (coach_id,),
            ).fetchone()

            # Conference record at the current school should cover every season
            # coached there, not merely the selected/current season. Team records
            # are the authoritative conference W/L/T store, while coach_seasons
            # identifies which program seasons belong to this coach.
            at_team_conf = connection.execute(
                """SELECT COALESCE(SUM(conference_wins),0) wins,
                          COALESCE(SUM(conference_losses),0) losses,
                          COALESCE(SUM(conference_ties),0) ties
                   FROM team_records
                   WHERE team_id=? AND season IN (
                       SELECT DISTINCT season FROM coach_seasons
                       WHERE coach_id=? AND team_id=?
                   )""",
                (int(team_id), coach_id, int(team_id)),
            ).fetchone()

            coach = {
                "coach_id": coach_id,
                "name": coach_name,
                "tenure_start": tenure_start,
                "tenure_years": tenure_years,
                "career_record": _record(career["wins"], career["losses"], career["ties"]),
                "team_record": _record(at_team["wins"], at_team["losses"], at_team["ties"]),
                "team_conf_record": _record(
                    at_team_conf["wins"], at_team_conf["losses"], at_team_conf["ties"]
                ),
            }

    elo_row = (repository.team_elo(int(season)).get(int(team_id)) or {})
    elo = elo_row.get("elo")

    return {
        "team": team,
        "elo": round(float(elo)) if elo is not None else None,
        "coach": coach,
    }


def coach_lineage_table(repository, team_id: int, through_season: int | None = None) -> Table:
    """Stored head-coach succession for a program, newest tenure first."""
    repository.initialize()
    params: list[Any] = [int(team_id)]
    where = "team_id=?"
    if through_season is not None:
        where += " AND season<=?"
        params.append(int(through_season))

    with closing(repository._connect()) as connection:
        rows = connection.execute(
            f"""SELECT season,coach_id,first_name,last_name,games,wins,losses,ties
                FROM coach_seasons WHERE {where}
                ORDER BY season ASC, games DESC""",
            params,
        ).fetchall()

    # Collapse consecutive seasons by the same coach into one tenure. If a coach
    # leaves and later returns, that becomes a separate lineage entry.
    tenures: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        season = int(row["season"])
        coach_id = int(row["coach_id"])
        if tenures and tenures[-1]["coach_id"] == coach_id and season == tenures[-1]["end"] + 1:
            tenure = tenures[-1]
            tenure["end"] = season
            tenure["games"] += int(row.get("games") or 0)
            tenure["wins"] += int(row.get("wins") or 0)
            tenure["losses"] += int(row.get("losses") or 0)
            tenure["ties"] += int(row.get("ties") or 0)
        else:
            tenures.append({
                "coach_id": coach_id,
                "coach": f"{row['first_name']} {row['last_name']}".strip(),
                "start": season,
                "end": season,
                "games": int(row.get("games") or 0),
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "ties": int(row.get("ties") or 0),
            })

    output = []
    for tenure in reversed(tenures):
        seasons = str(tenure["start"]) if tenure["start"] == tenure["end"] else f"{tenure['start']}–{tenure['end']}"
        games = int(tenure["games"])
        wins = int(tenure["wins"])
        pct = wins / games if games else None
        output.append({
            "seasons": seasons,
            "coach": tenure["coach"],
            "games": games,
            "record": _record(tenure["wins"], tenure["losses"], tenure["ties"]),
            "win_pct": pct,
        })

    return Table(
        columns=[
            Column("seasons", "Seasons"),
            Column("coach", "Head coach", emphasis=True),
            Column("games", "Games", format="int", align="right"),
            Column("record", "Record", align="right"),
            Column("win_pct", "Win %", format="pct", align="right"),
        ],
        rows=output,
        caption="Coach lineage",
        note="Consecutive stored seasons under the same head coach are collapsed into one tenure.",
        dense=True,
        sortable=True,
        empty="No stored coaching history is available for this program.",
    )
