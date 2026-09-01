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
    with repository._reader() as connection:
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


def _played(repository, team_id: int, *, first: int, last: int) -> list[dict[str, Any]]:
    """Completed games in a season range, with the consensus line where there is one.

    A left join, not an inner one: a game with no stored quote still counts
    towards what the team's games have produced, which is what `versus_total`
    reads. Only the spread and total records need a line to grade against.
    """
    with repository._reader() as connection:
        rows = connection.execute(
            # The consensus line is read per game rather than by joining a
            # grouped subquery: that form aggregated all 34,000 quotes in
            # `game_lines` before discarding all but one team's, six times per
            # matchup page. Correlating it costs one indexed lookup per game
            # actually returned -- 20ms against 1.1ms -- and AVG over no rows
            # is NULL, which is what the left join gave.
            """SELECT g.home_team_id, g.home_points, g.away_points,
                      (SELECT AVG(spread) FROM game_lines
                       WHERE game_id=g.game_id) spread,
                      (SELECT AVG(over_under) FROM game_lines
                       WHERE game_id=g.game_id) total
               FROM games g
               WHERE g.completed=1
                 AND g.home_points IS NOT NULL AND g.away_points IS NOT NULL
                 AND (g.home_team_id=? OR g.away_team_id=?)
                 AND g.season BETWEEN ? AND ?""",
            (team_id, team_id, first, last)).fetchall()
    played = []
    for row in rows:
        at_home = row["home_team_id"] == team_id
        played.append({
            "margin": ((row["home_points"] - row["away_points"]) if at_home
                       else (row["away_points"] - row["home_points"])),
            "points": row["home_points"] + row["away_points"],
            # The away side's line is the negation of the stored one.
            "spread": (None if row["spread"] is None
                       else (row["spread"] if at_home else -row["spread"])),
            "total": row["total"],
        })
    return played


def _grade(played: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A record against each game's own number."""
    covers = fails = pushes = 0
    overs = unders = total_pushes = 0
    for game in played:
        if game["spread"] is not None:
            verdict = _verdict(game["margin"], game["spread"])
            covers += verdict == "cover"
            fails += verdict == "fail"
            pushes += verdict == "push"
        if game["total"] is not None:
            if abs(game["points"] - game["total"]) < 1e-9:
                total_pushes += 1
            elif game["points"] > game["total"]:
                overs += 1
            else:
                unders += 1
    graded = covers + fails
    over_graded = overs + unders
    if not graded and not over_graded and not pushes and not total_pushes:
        return None
    return {
        "games": graded + pushes,
        "covers": covers, "fails": fails, "pushes": pushes,
        "cover_rate": round(100 * covers / graded, 1) if graded else None,
        "overs": overs, "unders": unders, "total_pushes": total_pushes,
        "over_rate": round(100 * overs / over_graded, 1) if over_graded else None,
        "provisional": (graded + pushes) < MINIMUM_GAMES,
        "ats_record": _record(covers, fails, pushes),
        "total_record": _record(overs, unders, total_pushes),
    }


def _role_grade(played: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    """ATS record from games played as a favorite or underdog.

    `spread` is already expressed from this team's perspective. Negative means
    the team laid points (favorite), positive means it received points (dog),
    and zero is a pick'em that belongs to neither split.
    """
    if role == "favorite":
        selected = [game for game in played
                    if game.get("spread") is not None and game["spread"] < 0]
    elif role == "underdog":
        selected = [game for game in played
                    if game.get("spread") is not None and game["spread"] > 0]
    else:
        return None
    return _grade(selected)


def _current_consensus_spread(repository, game_id: Any) -> float | None:
    """Current consensus spread stored for a game, from the home-team side."""
    if game_id is None:
        return None
    repository.initialize()
    with repository._reader() as connection:
        row = connection.execute(
            "SELECT AVG(spread) spread FROM game_lines WHERE game_id=?",
            (game_id,),
        ).fetchone()
    if not row or row["spread"] is None:
        return None
    return float(row["spread"])


def season_record(repository, team_id: int, *, season: int) -> dict[str, Any] | None:
    """This season only, each game graded against the number it was given."""
    repository.initialize()
    graded = _grade(_played(repository, team_id, first=season, last=season))
    return {**graded, "label": str(season), "span": str(season)} if graded else None


def against_the_spread(repository, team_id: int, *, season: int,
                       tenure: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One team's record against the spread and the total, under its coach."""
    tenure = tenure or coach_tenure(repository, team_id, season=season)
    if not tenure:
        return None
    played = _played(repository, team_id,
                     first=tenure["since"], last=tenure["through"])
    graded = _grade(played)
    if not graded:
        return None
    return {
        **tenure, **graded,
        "favorite": _role_grade(played, "favorite"),
        "underdog": _role_grade(played, "underdog"),
        "label": tenure["coach"] or "Current coach",
        "span": (f"{tenure['since']} or earlier" if tenure.get("truncated")
                 else f"since {tenure['since']}"),
    }


def versus_total(repository, team_id: int, *, season: int,
                 total: float | None) -> dict[str, Any] | None:
    """How this season's games would have landed against *this* game's total.

    Not each game against its own number -- that is what `season_record` says.
    This applies one number to every game the team has played, which answers
    whether tonight's total is a high one for this team or a low one.
    """
    if total is None:
        return None
    repository.initialize()
    played = _played(repository, team_id, first=season, last=season)
    if not played:
        return None
    over = sum(1 for game in played if game["points"] > total)
    under = sum(1 for game in played if game["points"] < total)
    push = len(played) - over - under
    return {
        "number": total, "over": over, "under": under, "push": push,
        "games": len(played),
        "label": f"Over {total:g}",
        "span": f"{season} games, this number applied to each",
        "record": _record(over, under, push),
        "rate": round(100 * over / (over + under), 1) if (over + under) else None,
    }


def _record(won: int, lost: int, pushed: int) -> str:
    """A record reads 9-4 when nothing pushed and 9-4-1 when something did."""
    return f"{won}-{lost}-{pushed}" if pushed else f"{won}-{lost}"


def _with_current_role(tenure: dict[str, Any] | None,
                       team_spread: float | None) -> dict[str, Any] | None:
    """Annotate a coach tenure record with the Fav/Dog split relevant today."""
    if not tenure or team_spread is None or abs(team_spread) < 1e-9:
        return tenure
    result = dict(tenure)
    role_key = "favorite" if team_spread < 0 else "underdog"
    role_label = "Fav" if role_key == "favorite" else "Dog"
    role = result.get(role_key)
    result["current_role"] = role_label
    result["current_role_record"] = role
    if role and role.get("ats_record"):
        result["span"] = f"{result['span']} · {role_label} {role['ats_record']} ATS"
    return result


def matchup_ats(repository, game: dict[str, Any], *,
                total: float | None = None) -> dict[str, Any]:
    """Three readings per side, for the page that shows the line.

    This season on its own, which is what the team is doing now; the current
    coach's whole tenure, including the favorite/underdog split relevant to the
    current matchup; and this game's total applied to every game played this
    year, which says whether tonight's number is a high one for this team.
    """
    season = int(game.get("season") or 0)
    home_spread = _current_consensus_spread(repository, game.get("game_id"))
    packet: dict[str, Any] = {}
    for prefix in ("away", "home"):
        team_id = game.get(f"{prefix}_team_id")
        if not team_id:
            continue
        team_spread = None
        if home_spread is not None:
            team_spread = home_spread if prefix == "home" else -home_spread
        tenure = against_the_spread(repository, team_id, season=season)
        packet[prefix] = {
            "season": season_record(repository, team_id, season=season),
            "tenure": _with_current_role(tenure, team_spread),
            "versus": versus_total(repository, team_id, season=season, total=total),
        }
    return packet
