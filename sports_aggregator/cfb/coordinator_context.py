"""Derived OC/DC continuity and performance context for team and matchup presentation."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.coordinators import initialize


_YARD_CATEGORIES = {"totalyards", "yards", "totaloffense"}


def _previous_stop(connection, current: dict[str, Any], season: int) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT season,team_id,team,side,role
           FROM coordinator_seasons
           WHERE coach_name=? AND side=? AND season<? AND team_id<>?
           ORDER BY season DESC LIMIT 1""",
        (current["coach_name"], current["side"], int(season), int(current["team_id"])),
    ).fetchone()
    return dict(row) if row is not None else None


def _game_yards(connection, game_id: int, team_id: int) -> float | None:
    """Return a team's total yards for one game from stored box stats.

    CFBD category spelling has varied over time, so normalize punctuation/case
    and accept the small set of total-offense labels seen in stored feeds.
    """
    rows = connection.execute(
        """SELECT category,numeric_value,stat_value
           FROM game_team_box_stats
           WHERE game_id=? AND team_id=?""",
        (int(game_id), int(team_id)),
    ).fetchall()
    for row in rows:
        category = "".join(ch for ch in str(row["category"] or "").casefold() if ch.isalnum())
        if category not in _YARD_CATEGORIES:
            continue
        value = row["numeric_value"] if row["numeric_value"] is not None else row["stat_value"]
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None
    return None


def _performance_for_assignments(connection, assignments: list[dict[str, Any]], side: str) -> dict[str, Any]:
    """Game-weighted performance over coordinator assignments.

    Offense uses points scored and offensive yards. Defense uses points and
    yards allowed. Averages are weighted by actual games, not by season, so an
    abbreviated assignment cannot count the same as a full season.
    """
    games = 0
    points_total = 0.0
    yard_games = 0
    yards_total = 0.0
    teams: set[str] = set()
    seasons: set[int] = set()

    for assignment in assignments:
        team_id = int(assignment["team_id"])
        season = int(assignment["season"])
        teams.add(str(assignment.get("team") or team_id))
        seasons.add(season)
        game_rows = connection.execute(
            """SELECT game_id,home_team_id,away_team_id,home_points,away_points
               FROM games
               WHERE season=? AND completed=1
                 AND home_points IS NOT NULL AND away_points IS NOT NULL
                 AND (home_team_id=? OR away_team_id=?)""",
            (season, team_id, team_id),
        ).fetchall()
        for game in game_rows:
            home = int(game["home_team_id"]) == team_id
            team_points = float(game["home_points"] if home else game["away_points"])
            opponent_points = float(game["away_points"] if home else game["home_points"])
            opponent_id = int(game["away_team_id"] if home else game["home_team_id"])
            games += 1
            points_total += team_points if side == "offense" else opponent_points

            yard_team_id = team_id if side == "offense" else opponent_id
            yards = _game_yards(connection, int(game["game_id"]), yard_team_id)
            if yards is not None:
                yard_games += 1
                yards_total += yards

    return {
        "games": games,
        "yard_games": yard_games,
        "seasons": len(seasons),
        "teams": len(teams),
        "points_per_game": round(points_total / games, 1) if games else None,
        "yards_per_game": round(yards_total / yard_games, 1) if yard_games else None,
        "points_label": "PPG" if side == "offense" else "PPG allowed",
        "yards_label": "YPG" if side == "offense" else "YPG allowed",
    }


def _career_performance(connection, current: dict[str, Any], season: int) -> dict[str, Any]:
    assignments = [dict(row) for row in connection.execute(
        """SELECT season,team_id,team,side
           FROM coordinator_seasons
           WHERE coach_name=? AND side=? AND season<=?
           ORDER BY season,team_id""",
        (current["coach_name"], current["side"], int(season)),
    ).fetchall()]
    current_team = [row for row in assignments if int(row["team_id"]) == int(current["team_id"])]
    return {
        "career": _performance_for_assignments(connection, assignments, str(current["side"])),
        "current_program": _performance_for_assignments(connection, current_team, str(current["side"])),
    }


def _side_context(connection, team_id: int, season: int, side: str) -> dict[str, Any] | None:
    raw = connection.execute(
        """SELECT season,team_id,team,side,role,coach_name,rating,experience_years,
                  source_name,source_url,verified_official,official_source_url
           FROM coordinator_seasons
           WHERE team_id=? AND season=? AND side=? LIMIT 1""",
        (int(team_id), int(season), side),
    ).fetchone()
    if raw is None:
        return None
    current = dict(raw)
    prior_raw = connection.execute(
        """SELECT season,team_id,team,side,role,coach_name
           FROM coordinator_seasons
           WHERE team_id=? AND season=? AND side=? LIMIT 1""",
        (int(team_id), int(season) - 1, side),
    ).fetchone()
    prior = dict(prior_raw) if prior_raw is not None else None
    changed = None if prior is None else prior["coach_name"] != current["coach_name"]

    tenure_start = int(season)
    cursor = int(season) - 1
    while True:
        row = connection.execute(
            "SELECT coach_name FROM coordinator_seasons WHERE team_id=? AND season=? AND side=? LIMIT 1",
            (int(team_id), cursor, side),
        ).fetchone()
        if row is None or str(row["coach_name"]) != str(current["coach_name"]):
            break
        tenure_start = cursor
        cursor -= 1
    tenure_years = int(season) - tenure_start + 1

    if changed is True:
        label = "New coordinator"
    elif changed is False and tenure_years >= 2:
        label = f"Year {tenure_years}"
    elif changed is False:
        label = "Returning coordinator"
    else:
        label = "Continuity unknown"

    performance = _career_performance(connection, current, season)
    return {
        "side": side,
        "role": current["role"],
        "coach_name": current["coach_name"],
        "name": current["coach_name"],
        "season": int(current["season"]),
        "tenure_start": tenure_start,
        "tenure_years": tenure_years,
        "changed": changed,
        "continuity_label": label,
        "previous_coordinator": prior["coach_name"] if changed is True and prior else None,
        "previous_stop": _previous_stop(connection, current, season),
        "career_performance": performance["career"],
        "program_performance": performance["current_program"],
        "rating": current.get("rating"),
        "experience_years": current.get("experience_years"),
        "source_name": current.get("source_name"),
        "source_url": current.get("source_url"),
        "verified_official": bool(current.get("verified_official")),
        "official_source_url": current.get("official_source_url"),
    }


def coordinator_context(repository, team_id: int, season: int) -> dict[str, Any]:
    """Return current coordinator, continuity, and career performance context."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        offense = _side_context(connection, team_id, season, "offense")
        defense = _side_context(connection, team_id, season, "defense")
    known = [x for x in (offense, defense) if x is not None]
    known_changes = [x for x in known if x["changed"] is not None]
    return {
        "season": int(season),
        "team_id": int(team_id),
        "offense": offense,
        "defense": defense,
        "has_complete_staff": len(known) == 2,
        "change_count": sum(1 for x in known_changes if x["changed"]) if known_changes else None,
        "known_change_sides": len(known_changes),
        "continuity_score": sum(min(int(x["tenure_years"]), 4) for x in known) if len(known) == 2 else None,
        "both_return": all(x["changed"] is False for x in known_changes) if len(known_changes) == 2 else None,
    }


def coordinator_matchup_context(repository, away_team_id: int, home_team_id: int, season: int) -> dict[str, Any]:
    """Prepare a symmetric coordinator packet for matchup-page comparison."""
    away = coordinator_context(repository, away_team_id, season)
    home = coordinator_context(repository, home_team_id, season)
    away_score, home_score = away.get("continuity_score"), home.get("continuity_score")
    edge = None
    if away_score is not None and home_score is not None:
        edge = "away" if away_score > home_score else "home" if home_score > away_score else "even"
    return {
        "season": int(season),
        "away": away,
        "home": home,
        "continuity_edge": edge,
        "continuity_score_delta": away_score - home_score if away_score is not None and home_score is not None else None,
    }
