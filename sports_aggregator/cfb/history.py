"""Derived historical game, matchup, and position-production context."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sports_aggregator.cfb.repository import CFBRepository


EASTERN = ZoneInfo("America/New_York")


def _local_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def time_slot(value: str) -> str:
    """Editorial kickoff bucket, based on the US Eastern broadcast window."""
    start = _local_start(value)
    weekday = start.strftime("%A")
    if weekday != "Saturday":
        return weekday
    minutes = start.hour * 60 + start.minute
    if minutes < 15 * 60 + 30:
        return "Saturday — day"
    if minutes < 19 * 60:
        return "Saturday — afternoon"
    if minutes < 22 * 60:
        return "Saturday — primetime"
    return "Saturday — late night"


def _perspective(game: dict[str, Any], team_id: int) -> dict[str, Any]:
    home = game["home_team_id"] == team_id
    points_for = game["home_points"] if home else game["away_points"]
    points_against = game["away_points"] if home else game["home_points"]
    opponent_id = game["away_team_id"] if home else game["home_team_id"]
    opponent = game["away_team"] if home else game["home_team"]
    opponent_conference = game["away_conference"] if home else game["home_conference"]
    if points_for is None or points_against is None:
        result = None
    elif points_for > points_against:
        result = "W"
    elif points_for < points_against:
        result = "L"
    else:
        result = "T"
    return {
        **game, "team_id": team_id, "opponent_id": opponent_id,
        "opponent": opponent, "opponent_conference": opponent_conference,
        "points_for": points_for, "points_against": points_against,
        "result": result, "site": "Neutral" if game["neutral_site"] else
        ("Home" if home else "Away"), "slot": time_slot(game["start_date"]),
    }


def _record(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = [row for row in rows if row.get("result")]
    wins = sum(row["result"] == "W" for row in items)
    losses = sum(row["result"] == "L" for row in items)
    ties = sum(row["result"] == "T" for row in items)
    games = len(items)
    points_for = sum(int(row["points_for"]) for row in items)
    points_against = sum(int(row["points_against"]) for row in items)
    return {
        "games": games, "wins": wins, "losses": losses, "ties": ties,
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "ppg_for": round(points_for / games, 1) if games else None,
        "ppg_against": round(points_against / games, 1) if games else None,
        "average_margin": round((points_for - points_against) / games, 1) if games else None,
    }


def _streak(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    latest = sorted(rows, key=lambda row: row["start_date"], reverse=True)
    result = latest[0].get("result")
    if not result:
        return None
    length = 0
    for row in latest:
        if row.get("result") != result:
            break
        length += 1
    return f"{result}{length}"


def _completed_games(repository: CFBRepository, *, before: str | None = None
                     ) -> list[dict[str, Any]]:
    repository.initialize()
    sql = "SELECT * FROM games WHERE completed=1 AND home_points IS NOT NULL AND away_points IS NOT NULL"
    params: list[Any] = []
    if before:
        sql += " AND start_date<?"
        params.append(before)
    sql += " ORDER BY start_date DESC"
    with closing(repository._connect()) as connection:
        return [dict(row) for row in connection.execute(sql, params)]


def matchup_history(repository: CFBRepository, game: dict[str, Any],
                    recent_limit: int = 10) -> dict[str, Any]:
    """History packet for one scheduled or completed matchup."""
    all_games = _completed_games(repository, before=game["start_date"])
    away_id, home_id = game["away_team_id"], game["home_team_id"]
    meetings = [row for row in all_games if {row["home_team_id"], row["away_team_id"]}
                == {home_id, away_id}]
    away_meetings = [_perspective(row, away_id) for row in meetings]
    home_meetings = [_perspective(row, home_id) for row in meetings]
    away_all = [_perspective(row, away_id) for row in all_games
                if away_id in (row["home_team_id"], row["away_team_id"])]
    home_all = [_perspective(row, home_id) for row in all_games
                if home_id in (row["home_team_id"], row["away_team_id"])]
    slot = time_slot(game["start_date"])

    def context(team_id: int, rows: list[dict[str, Any]], opponent_conference: str | None,
                opponent_id: int) -> dict[str, Any]:
        slot_rows = [row for row in rows if row["slot"] == slot]
        target_site = "Neutral" if game["neutral_site"] else (
            "Home" if game["home_team_id"] == team_id else "Away")
        site_rows = [row for row in rows if row["opponent_id"] == opponent_id
                     and row["site"] == target_site]
        conference_rows = [row for row in rows
                           if opponent_conference and row["opponent_conference"] == opponent_conference]
        unique_opponents = len({row["opponent_id"] for row in rows})
        return {
            "overall": _record(rows), "slot": {**_record(slot_rows), "label": slot},
            "site": {**_record(site_rows), "label": target_site},
            "conference": {**_record(conference_rows), "conference": opponent_conference},
            "unique_opponents": unique_opponents,
            "coach": _coach_record(repository, team_id, opponent_id,
                                    game["season"], game["start_date"]),
        }

    recent = []
    for row in sorted(away_meetings, key=lambda item: item["start_date"], reverse=True)[:recent_limit]:
        local = _local_start(row["start_date"])
        recent.append({
            **row, "date_label": local.strftime("%b %d, %Y"),
            "score": f"{row['points_for']}-{row['points_against']}",
            "game_url": f"/college-football/games/{row['game_id']}/",
        })
    coverage = sorted({row["season"] for row in all_games})
    return {
        "meetings": len(meetings), "first_meeting": min((row["season"] for row in meetings), default=None),
        "last_meeting": max((row["season"] for row in meetings), default=None),
        "away_record": {**_record(away_meetings), "streak": _streak(away_meetings)},
        "home_record": {**_record(home_meetings), "streak": _streak(home_meetings)},
        "away_context": context(away_id, away_all, game.get("home_conference"), home_id),
        "home_context": context(home_id, home_all, game.get("away_conference"), away_id),
        "slot": slot, "recent": recent,
        "coverage": {"from": min(coverage) if coverage else None,
                     "through": max(coverage) if coverage else None},
    }


def _coach_record(repository: CFBRepository, team_id: int, opponent_id: int,
                  season: int, before: str) -> dict[str, Any] | None:
    with closing(repository._connect()) as connection:
        current = connection.execute(
            """SELECT * FROM coach_seasons WHERE team_id=? AND season<=?
               ORDER BY season DESC LIMIT 1""", (team_id, season)).fetchone()
        if current is None:
            return None
        attributed = [dict(row) for row in connection.execute(
            "SELECT season,team_id FROM coach_seasons WHERE coach_id=?",
            (current["coach_id"],))]
        game_rows = [dict(row) for row in connection.execute(
            """SELECT * FROM games WHERE completed=1 AND start_date<? AND
               (home_team_id=? OR away_team_id=?)""",
            (before, opponent_id, opponent_id))]
    # A coach's record against an opponent follows him across jobs. CFBD's
    # season attribution is the source boundary, so a mid-season handoff is not
    # represented as exact unless the upstream season attribution is exact.
    coached = {(row["season"], row["team_id"]) for row in attributed}
    relevant = []
    for row in game_rows:
        for coached_season, coached_team in coached:
            if row["season"] != coached_season:
                continue
            if coached_team not in (row["home_team_id"], row["away_team_id"]):
                continue
            perspective = _perspective(row, coached_team)
            if perspective["opponent_id"] == opponent_id:
                relevant.append(perspective)
            break
    record = _record(relevant)
    return {**record, "coach_id": current["coach_id"],
            "name": f"{current['first_name']} {current['last_name']}".strip(),
            "through_season": current["season"], "attribution": "team-season"}


def team_game_history(repository: CFBRepository, team_id: int,
                      season: int | None = None) -> dict[str, Any]:
    team = repository.get_team(team_id)
    if team is None:
        return {"team": None, "games": [], "seasons": [], "summary": _record([])}
    games = _completed_games(repository)
    rows = [_perspective(row, team_id) for row in games
            if team_id in (row["home_team_id"], row["away_team_id"])]
    seasons = sorted({row["season"] for row in rows}, reverse=True)
    selected = [row for row in rows if season is None or row["season"] == season]
    for row in selected:
        local = _local_start(row["start_date"])
        row["date_label"] = local.strftime("%b %d, %Y")
        row["score"] = f"{row['points_for']}-{row['points_against']}"
        row["game_url"] = f"/college-football/games/{row['game_id']}/"
    by_season = []
    for year in seasons:
        year_rows = [row for row in rows if row["season"] == year]
        by_season.append({"season": year, **_record(year_rows)})
    return {"team": team, "games": selected, "seasons": seasons,
            "selected_season": season, "summary": _record(selected),
            "season_summaries": by_season,
            "unique_opponents": len({row["opponent_id"] for row in selected})}


POSITION_GROUPS = {
    "QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
    "OL": "OL", "OT": "OL", "OG": "OL", "C": "OL", "G": "OL", "T": "OL",
    "DL": "DL", "DT": "DL", "DE": "EDGE", "EDGE": "EDGE",
    "LB": "LB", "ILB": "LB", "OLB": "LB",
    "CB": "SECONDARY", "S": "SECONDARY", "DB": "SECONDARY",
    "K": "SPECIALISTS", "P": "SPECIALISTS", "LS": "SPECIALISTS",
}


def team_historical_stats(repository: CFBRepository, team_id: int) -> dict[str, Any]:
    team = repository.get_team(team_id)
    if team is None:
        return {"team": None, "seasons": [], "positions": [], "identity": []}
    repository.initialize()
    with closing(repository._connect()) as connection:
        records = [dict(row) for row in connection.execute(
            """SELECT r.*,a.offense_success_rate,a.defense_success_rate,
                      a.offense_ppa,a.defense_ppa
               FROM team_records r LEFT JOIN team_advanced_stats a
                 ON a.season=r.season AND a.team=r.team
               WHERE r.team_id=? ORDER BY r.season DESC""", (team_id,))]
        games = [dict(row) for row in connection.execute(
            """SELECT * FROM games WHERE completed=1 AND
               (home_team_id=? OR away_team_id=?)""", (team_id, team_id))]
        stats = [dict(row) for row in connection.execute(
            """SELECT season,position,category,stat_type,SUM(numeric_value) value
               FROM player_season_stats WHERE team=? AND numeric_value IS NOT NULL
               GROUP BY season,position,category,stat_type""", (team["school"],))]
        pff = [dict(row) for row in connection.execute(
            """SELECT season,position_group,dataset,weighted_grade,player_count
               FROM pff_position_groups WHERE cfbd_team_id=? AND weighted_grade IS NOT NULL
               ORDER BY season DESC""", (team_id,))]
        raw_team_stats = [dict(row) for row in connection.execute(
            """SELECT season,stat_name,stat_value FROM team_stats
               WHERE team=? ORDER BY season DESC,stat_name""", (team["school"],))]
    game_rows = [_perspective(row, team_id) for row in games]
    season_games: dict[int, list[dict[str, Any]]] = {}
    for row in game_rows:
        season_games.setdefault(row["season"], []).append(row)
    record_by_year = {row["season"]: row for row in records}
    years = sorted(set(record_by_year) | set(season_games), reverse=True)
    seasons = []
    for year in years:
        derived = _record(season_games.get(year, []))
        stored = record_by_year.get(year, {})
        seasons.append({**stored, **derived, "season": year})

    stat_pivot: dict[int, dict[str, float]] = {}
    for row in raw_team_stats:
        try:
            value = float(row["stat_value"])
        except (TypeError, ValueError):
            continue
        stat_pivot.setdefault(row["season"], {})[row["stat_name"]] = value
    team_stats = []
    for year in sorted(stat_pivot, reverse=True):
        values = stat_pivot[year]
        games_played = len(season_games.get(year, [])) or None
        per_game = lambda name: (round(values[name] / games_played, 1)
                                 if games_played and name in values else None)
        team_stats.append({
            "season": year, "games": games_played,
            "yards_per_game": per_game("totalYards"),
            "pass_yards_per_game": per_game("netPassingYards"),
            "rush_yards_per_game": per_game("rushingYards"),
            "opponent_yards_per_game": per_game("totalYardsOpponent"),
            "sacks": values.get("sacks"), "tackles_for_loss": values.get("tacklesForLoss"),
            "turnover_margin": ((values.get("turnoversOpponent") or 0) -
                                (values.get("turnovers") or 0)
                                if "turnoversOpponent" in values and "turnovers" in values
                                else None),
        })

    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for row in stats:
        group = POSITION_GROUPS.get(str(row.get("position") or "").upper(), "OTHER")
        entry = grouped.setdefault((row["season"], group), {
            "season": row["season"], "position_group": group,
            "pass_yards": 0.0, "rush_yards": 0.0, "receiving_yards": 0.0,
            "touchdowns": 0.0, "receptions": 0.0, "tackles": 0.0,
            "sacks": 0.0, "interceptions": 0.0,
        })
        category = str(row["category"]).casefold()
        stat_type = str(row["stat_type"]).upper()
        value = float(row["value"] or 0)
        if category == "passing" and stat_type == "YDS": entry["pass_yards"] += value
        if category == "rushing" and stat_type == "YDS": entry["rush_yards"] += value
        if category == "receiving" and stat_type == "YDS": entry["receiving_yards"] += value
        if stat_type in ("TD", "TDS"): entry["touchdowns"] += value
        if category == "receiving" and stat_type in ("REC", "RECEPTIONS"): entry["receptions"] += value
        if stat_type in ("TOT", "TOTAL", "TACKLES"): entry["tackles"] += value
        if stat_type in ("SACKS", "SACK"): entry["sacks"] += value
        if category in ("interceptions", "defensive") and stat_type in ("INT", "INTS"):
            entry["interceptions"] += value
    totals: dict[int, dict[str, float]] = {}
    for (year, _group), row in grouped.items():
        total = totals.setdefault(year, {"rush_yards": 0, "receiving_yards": 0,
                                         "tackles": 0, "sacks": 0})
        for name in total:
            total[name] += row[name]
    pff_by_group: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in pff:
        production_group = {"INTERIOR_DL": "DL"}.get(
            row["position_group"], row["position_group"])
        pff_by_group.setdefault((row["season"], production_group), []).append(row)
    positions = []
    for key, row in sorted(grouped.items(), key=lambda item: (-item[0][0], item[0][1])):
        grades = pff_by_group.get(key, [])
        row["pff_grade"] = max((item["weighted_grade"] for item in grades), default=None)
        row["pff_detail"] = "; ".join(
            f"{item['dataset'].replace('_', ' ')} {item['weighted_grade']:.1f}"
            for item in sorted(grades, key=lambda item: -item["weighted_grade"])) or None
        row["pff_samples"] = sum(item["player_count"] for item in grades) if grades else None
        for stat in ("rush_yards", "receiving_yards", "tackles", "sacks"):
            denominator = totals[row["season"]][stat]
            row[f"{stat}_share"] = round(100 * row[stat] / denominator, 1) if denominator else None
        positions.append(row)
    latest = max((row["season"] for row in positions), default=None)
    identity = [row for row in positions if row["season"] == latest]
    return {"team": team, "seasons": seasons, "team_stats": team_stats,
            "positions": positions,
            "identity": identity, "latest_production_season": latest,
            "pff_seasons": sorted({row["season"] for row in pff}, reverse=True)}
