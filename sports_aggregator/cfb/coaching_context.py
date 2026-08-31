"""Team-page coaching context and historical coach lineage."""

from __future__ import annotations

from contextlib import closing
from typing import Any
from urllib.parse import quote

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.rivalries import rivalries_for_team
from sports_aggregator.tables import Column, Table


def _record(wins: int | None, losses: int | None, ties: int | None = 0) -> str:
    wins = int(wins or 0)
    losses = int(losses or 0)
    ties = int(ties or 0)
    return f"{wins}-{losses}" + (f"-{ties}" if ties else "")


def _rivalry_wiki_url(rivalry: dict[str, Any]) -> str:
    """Direct Wikipedia URL for the canonical two-program rivalry article.

    Wikipedia commonly redirects pair-name rivalry URLs to the better-known
    nickname article (Iron Bowl, The Game, Apple Cup, etc.), so this gives the
    UI a durable direct wiki target without making a request while rendering.
    """
    teams = rivalry.get("teams") or ()
    if len(teams) >= 2:
        title = f"{teams[0]}–{teams[1]} football rivalry"
    else:
        title = str(rivalry.get("name") or "college football rivalry")
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_-")


def _resolve_team(connection, name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    row = connection.execute(
        "SELECT team_id,school FROM teams WHERE school=? LIMIT 1", (str(name),)
    ).fetchone()
    if row is None:
        row = connection.execute(
            """SELECT t.team_id,t.school
               FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
               WHERE a.normalized_alias=? LIMIT 1""",
            (normalize_alias(str(name)),),
        ).fetchone()
    return dict(row) if row is not None else None


def _series_record(connection, team_id: int, opponent_id: int) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT home_team_id,away_team_id,home_points,away_points,season
           FROM games
           WHERE completed=1 AND home_points IS NOT NULL AND away_points IS NOT NULL
             AND ((home_team_id=? AND away_team_id=?) OR
                  (home_team_id=? AND away_team_id=?))
           ORDER BY start_date""",
        (team_id, opponent_id, opponent_id, team_id),
    ).fetchall()
    wins = losses = ties = 0
    first = last = None
    for raw in rows:
        row = dict(raw)
        home = int(row["home_team_id"]) == int(team_id)
        team_points = row["home_points"] if home else row["away_points"]
        opponent_points = row["away_points"] if home else row["home_points"]
        if team_points > opponent_points:
            wins += 1
        elif team_points < opponent_points:
            losses += 1
        else:
            ties += 1
        year = int(row["season"])
        first = year if first is None else min(first, year)
        last = year if last is None else max(last, year)
    return {
        "series_record": _record(wins, losses, ties),
        "series_games": len(rows),
        "stored_first_meeting": first,
        "stored_last_meeting": last,
    }


def _program_history(connection, team_id: int, team: dict[str, Any]) -> dict[str, Any]:
    """Fast stored-history summary for the team-page header.

    This intentionally says *stored* history. It derives directly from completed
    game rows rather than pretending the local backfill is an NCAA all-time
    record book. Wikipedia/NCAA first-season and championship facts can enrich
    this packet later without changing the template contract.
    """
    row = connection.execute(
        """SELECT MIN(season) first_season, MAX(season) last_season,
                  COUNT(*) games,
                  SUM(CASE
                        WHEN home_team_id=? AND home_points>away_points THEN 1
                        WHEN away_team_id=? AND away_points>home_points THEN 1
                        ELSE 0 END) wins,
                  SUM(CASE
                        WHEN home_team_id=? AND home_points<away_points THEN 1
                        WHEN away_team_id=? AND away_points<home_points THEN 1
                        ELSE 0 END) losses,
                  SUM(CASE WHEN home_points=away_points THEN 1 ELSE 0 END) ties
           FROM games
           WHERE completed=1 AND home_points IS NOT NULL AND away_points IS NOT NULL
             AND (home_team_id=? OR away_team_id=?)""",
        (team_id, team_id, team_id, team_id, team_id, team_id),
    ).fetchone()
    history = dict(row) if row is not None else {}
    rivalries = []
    school = str(team.get("school") or "")
    for seeded in rivalries_for_team(school):
        rivalry = dict(seeded)
        teams = list(rivalry.get("teams") or ())
        opponent_seed = next(
            (name for name in teams if normalize_alias(name) != normalize_alias(school)),
            None,
        )
        opponent = _resolve_team(connection, opponent_seed)
        rivalry["opponent"] = (opponent or {}).get("school") or opponent_seed
        rivalry["wiki_url"] = _rivalry_wiki_url(rivalry)
        if opponent:
            rivalry.update(_series_record(connection, int(team_id), int(opponent["team_id"])))
        else:
            rivalry.update({
                "series_record": "—", "series_games": 0,
                "stored_first_meeting": None, "stored_last_meeting": None,
            })
        rivalries.append(rivalry)

    history.update({
        "record": _record(history.get("wins"), history.get("losses"), history.get("ties")),
        "venue": team.get("venue_name"),
        "rivalry_count": len(rivalries),
        "rivalries": rivalries,
    })
    return history


def team_coaching_context(repository, team_id: int, season: int) -> dict[str, Any]:
    """Current Elo plus all-time head-coach and stored program context."""
    repository.initialize()
    team = repository.get_team(int(team_id))
    if team is None:
        return {}

    with repository._reader() as connection:
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

        program = _program_history(connection, int(team_id), team)

    elo_rows = repository.team_elo(int(season))
    elo_row = elo_rows.get(int(team_id)) or {}
    elo = elo_row.get("elo")
    elo_rank = None
    if elo is not None:
        rating = float(elo)
        elo_rank = 1 + sum(
            1 for row in elo_rows.values()
            if row.get("elo") is not None and float(row["elo"]) > rating
        )

    elo_value = round(float(elo)) if elo is not None else None
    elo_display = (
        f"{elo_value} · #{elo_rank}" if elo_value is not None and elo_rank is not None
        else (str(elo_value) if elo_value is not None else None)
    )
    return {
        "team": team,
        "elo": elo_display,
        "elo_value": elo_value,
        "elo_rank": elo_rank,
        "coach": coach,
        "program": program,
    }


def coach_lineage_table(repository, team_id: int, through_season: int | None = None) -> Table:
    """Stored head-coach succession for a program, newest tenure first."""
    repository.initialize()
    params: list[Any] = [int(team_id)]
    where = "team_id=?"
    if through_season is not None:
        where += " AND season<=?"
        params.append(int(through_season))

    with repository._reader() as connection:
        rows = connection.execute(
            f"""SELECT season,coach_id,first_name,last_name,games,wins,losses,ties
                FROM coach_seasons WHERE {where}
                ORDER BY season ASC, games DESC""",
            params,
        ).fetchall()

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
