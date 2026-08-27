"""How a team has done against the number, under the coach it has now.

A line is the market's forecast, and the useful question beside it is how often
this team has beaten that forecast lately. "Lately" has to mean something, and
a coaching change is the sharpest break a program has: the scheme, the staff
and usually half the roster turn over at once, so a record that reaches back
past one is measuring somebody else's team.

The tenure is read from the coach seasons already stored, walking back from the
current season while the same coach holds the job. Everything here is a record
of what happened, not a prediction, and no betting advice is derived from it.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

#: Fewest games before a record is worth showing.
#:
#: A first-year coach eight games in is a small sample presented as a rate, and
#: "4-4 ATS" invites a reader to conclude something it cannot support. Below
#: this the record is still returned, with `provisional` set, so a caller can
#: show it quietly or not at all.
MINIMUM_GAMES = 12


def coach_tenure(repository, team_id: int, *, season: int) -> dict[str, Any] | None:
    """The current coach at a team, and the first season of this spell.

    A second spell is a separate tenure: the walk back stops at any season the
    job changed hands, so a coach returning after a gap starts again.
    """
    repository.initialize()
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            """SELECT season, coach_id, first_name, last_name
               FROM coach_seasons WHERE team_id=? AND season<=?
               ORDER BY season DESC""", (team_id, season)).fetchall()
        horizon = connection.execute(
            "SELECT MIN(season) FROM coach_seasons").fetchone()[0]
    if not rows:
        return None
    current = rows[0]
    if not current["coach_id"]:
        return None
    first_season = current["season"]
    previous = current["season"]
    for row in rows[1:]:
        if row["coach_id"] != current["coach_id"]:
            break
        # A gap in the record is a gap in the job, not a season to absorb.
        if previous - row["season"] > 1:
            break
        first_season = previous = row["season"]
    name = " ".join(part for part in (current["first_name"], current["last_name"]) if part)
    return {
        "coach_id": current["coach_id"], "coach": name.strip() or None,
        "since": first_season, "through": current["season"],
        # A tenure that reaches the oldest season stored may well start before
        # it. Kirby Smart took Georgia in 2016 and the coach seasons begin in
        # 2019, so "since 2019" would be this database's horizon presented as a
        # fact about him. The record is real; its start date is a floor.
        "truncated": horizon is not None and first_season <= horizon,
    }


def _verdict(margin: float, spread: float) -> str:
    """Whether a team beat its own number.

    The stored spread is signed against the home team, so the home side's line
    is the number itself and the away side's is its negation. A game landing
    exactly on it is a push, which is neither a win nor a loss and is counted
    apart rather than folded into either.
    """
    edge = margin + spread
    if abs(edge) < 1e-9:
        return "push"
    return "cover" if edge > 0 else "fail"


def against_the_spread(repository, team_id: int, *, season: int,
                       tenure: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One team's record against the spread and the total, under its coach."""
    tenure = tenure or coach_tenure(repository, team_id, season=season)
    if not tenure:
        return None
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            """SELECT g.home_team_id, g.home_points, g.away_points,
                      AVG(l.spread) spread, AVG(l.over_under) total
               FROM games g JOIN game_lines l ON l.game_id=g.game_id
               WHERE g.completed=1
                 AND g.home_points IS NOT NULL AND g.away_points IS NOT NULL
                 AND (g.home_team_id=? OR g.away_team_id=?)
                 AND g.season BETWEEN ? AND ?
               GROUP BY g.game_id""",
            (team_id, team_id, tenure["since"], tenure["through"])).fetchall()

    covers = fails = pushes = 0
    overs = unders = total_pushes = 0
    for row in rows:
        at_home = row["home_team_id"] == team_id
        margin = ((row["home_points"] - row["away_points"]) if at_home
                  else (row["away_points"] - row["home_points"]))
        if row["spread"] is not None:
            # The away side's line is the negation of the stored one.
            spread = row["spread"] if at_home else -row["spread"]
            verdict = _verdict(margin, spread)
            covers += verdict == "cover"
            fails += verdict == "fail"
            pushes += verdict == "push"
        if row["total"] is not None:
            points = row["home_points"] + row["away_points"]
            if abs(points - row["total"]) < 1e-9:
                total_pushes += 1
            elif points > row["total"]:
                overs += 1
            else:
                unders += 1

    graded = covers + fails
    over_graded = overs + unders
    if not graded and not over_graded:
        return None
    return {
        **tenure,
        "games": graded + pushes,
        "covers": covers, "fails": fails, "pushes": pushes,
        "cover_rate": round(100 * covers / graded, 1) if graded else None,
        "overs": overs, "unders": unders, "total_pushes": total_pushes,
        "over_rate": round(100 * overs / over_graded, 1) if over_graded else None,
        # Said rather than hidden: a rate over eight games is not a tendency.
        "provisional": (graded + pushes) < MINIMUM_GAMES,
        "ats_record": _record(covers, fails, pushes),
        "total_record": _record(overs, unders, total_pushes),
        "span": (f"{tenure['since']} or earlier" if tenure.get("truncated")
                 else f"since {tenure['since']}"),
    }


def _record(won: int, lost: int, pushed: int) -> str:
    """A record reads 9-4 when nothing pushed and 9-4-1 when something did."""
    return f"{won}-{lost}-{pushed}" if pushed else f"{won}-{lost}"


def matchup_ats(repository, game: dict[str, Any]) -> dict[str, Any]:
    """Both sides of one game, for the page that shows the line."""
    season = int(game.get("season") or 0)
    return {
        prefix: against_the_spread(
            repository, game.get(f"{prefix}_team_id"), season=season)
        for prefix in ("away", "home")
        if game.get(f"{prefix}_team_id")
    }
