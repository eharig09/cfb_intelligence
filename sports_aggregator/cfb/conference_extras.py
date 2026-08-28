"""Conference leaderboard enrichment and schedule-Elo summaries."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb import views
from sports_aggregator.cfb.coaching_context import coach_lineage_table, team_coaching_context
from sports_aggregator.cfb.models import normalize_person_name
from sports_aggregator.cfb.statlines import category_label, sort_stat


CONFERENCE_MINIMUMS: dict[str, tuple[str, float]] = {
    "passing": ("ATT", 25),
    "rushing": ("CAR", 20),
    "receiving": ("REC", 5),
    "defensive": ("TOT", 20),
    "interceptions": ("INT", 1),
    "fumbles": ("FUM", 1),
    "kicking": ("FGA", 5),
    "punting": ("NO", 10),
    "kickReturns": ("NO", 3),
    "puntReturns": ("NO", 3),
}


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def team_schedule_elo(repository, team_id: int, season: int) -> dict[str, Any]:
    """Current-opponent Elo plus current program/coach context for one team.

    Home/away values use remaining non-neutral games only. Conference and
    non-conference values use the full stored schedule.
    """
    current_elo = repository.team_elo(season)
    schedule = repository.team_schedule(team_id, season)
    remaining_home: list[float] = []
    remaining_away: list[float] = []
    conference_values: list[float] = []
    nonconference_values: list[float] = []

    for game in schedule:
        home = game.get("home_team_id") == team_id
        opponent_id = game.get("away_team_id") if home else game.get("home_team_id")
        rating = (current_elo.get(opponent_id) or {}).get("elo")
        if rating is None:
            continue
        value = float(rating)
        if game.get("conference_game"):
            conference_values.append(value)
        else:
            nonconference_values.append(value)
        if not game.get("completed") and not game.get("neutral_site"):
            (remaining_home if home else remaining_away).append(value)

    coaching = team_coaching_context(repository, team_id, season)
    return {
        "remaining_home": _average(remaining_home),
        "remaining_home_games": len(remaining_home),
        "remaining_away": _average(remaining_away),
        "remaining_away_games": len(remaining_away),
        "conference": _average(conference_values),
        "conference_games": len(conference_values),
        "nonconference": _average(nonconference_values),
        "nonconference_games": len(nonconference_values),
        "elo": coaching.get("elo"),
        "elo_value": coaching.get("elo_value"),
        "elo_rank": coaching.get("elo_rank"),
        "coach": coaching.get("coach"),
        "program": coaching.get("program") or {},
        "coach_lineage": coach_lineage_table(repository, team_id, season),
    }


def _category_from_group(group: dict[str, Any]) -> str | None:
    if group.get("category"):
        return str(group["category"])
    label = str(group.get("label") or "")
    for candidate in CONFERENCE_MINIMUMS:
        if category_label(candidate) == label:
            return candidate
    return None


def _qualifies(category: str, stats: dict[str, Any]) -> bool:
    minimum = CONFERENCE_MINIMUMS.get(category)
    if not minimum:
        return True
    key, threshold = minimum
    try:
        return float(stats.get(key) or 0) >= threshold
    except (TypeError, ValueError):
        return False


def _conference_members(connection, conference: str) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT school FROM teams WHERE conference=?", (conference,)
    )}


def _team_conferences(connection) -> dict[str, str | None]:
    return {row[0]: row[1] for row in connection.execute(
        "SELECT school,conference FROM teams"
    )}


def _incoming_transfer_rows(connection, conference: str, season: int,
                            source_season: int) -> list[dict[str, Any]]:
    members = _conference_members(connection, conference)
    team_conferences = _team_conferences(connection)
    transfers = [dict(row) for row in connection.execute(
        """SELECT normalized_name,first_name,last_name,position,origin,destination
           FROM player_transfers WHERE season=? AND destination IS NOT NULL""",
        (season,),
    ) if row["destination"] in members and team_conferences.get(row["origin"]) != conference]

    incoming: list[dict[str, Any]] = []
    for movement in transfers:
        stat_rows = [dict(row) for row in connection.execute(
            """SELECT player_id,player,team,position,category,stat_type,stat_value,numeric_value
               FROM player_season_stats WHERE season=? AND team=?""",
            (source_season, movement["origin"]),
        )]
        matched = [row for row in stat_rows
                   if normalize_person_name(row["player"]) == movement["normalized_name"]]
        if not matched:
            continue
        by_category: dict[str, dict[str, Any]] = {}
        for row in matched:
            bucket = by_category.setdefault(row["category"], {
                "player_id": row["player_id"],
                "player": row["player"],
                "position": row.get("position") or movement.get("position"),
                "team": movement["destination"],
                "stats": {},
            })
            bucket["stats"][row["stat_type"]] = (
                row["numeric_value"] if row["numeric_value"] is not None else row["stat_value"]
            )
        origin_conf = team_conferences.get(movement["origin"]) or "Independent"
        for category, entry in by_category.items():
            if category not in CONFERENCE_MINIMUMS or not _qualifies(category, entry["stats"]):
                continue
            entry.update({
                "category": category,
                "origin": movement["origin"],
                "origin_conference": origin_conf,
                "destination": movement["destination"],
            })
            incoming.append(entry)
    return incoming


def conference_leader_packet(repository, conference: str, season: int) -> dict[str, Any]:
    """Full qualified conference leaderboards with transfer-only annotations."""
    leaders = repository.conference_player_leaders(conference, season, limit=250)
    leader_groups_raw = leaders.get("groups") or {}
    has_rows = any(group.get("players") for group in leader_groups_raw.values())
    if not has_rows and season > 0:
        leaders = repository.conference_player_leaders(conference, season - 1, limit=250)
    source_season = int(leaders.get("season") or season)
    groups = views.leader_groups(leaders, season)

    repository.initialize()
    with closing(repository._connect()) as connection:
        members = _conference_members(connection, conference)
        team_conferences = _team_conferences(connection)
        transfers = [dict(row) for row in connection.execute(
            """SELECT normalized_name,origin,destination FROM player_transfers
               WHERE season=?""", (season,)
        )]
        incoming = _incoming_transfer_rows(connection, conference, season, source_season)

    movement_by_name: dict[str, list[dict[str, Any]]] = {}
    for movement in transfers:
        movement_by_name.setdefault(movement["normalized_name"], []).append(movement)

    incoming_by_category: dict[str, list[dict[str, Any]]] = {}
    for entry in incoming:
        incoming_by_category.setdefault(entry["category"], []).append(entry)

    for group in groups:
        category = _category_from_group(group)
        table = group.get("table")
        if not category or table is None:
            continue

        filtered = []
        for row in table.rows:
            stats = {key: row.get(key) for key in row.keys()}
            if not _qualifies(category, stats):
                continue
            movements = movement_by_name.get(normalize_person_name(str(row.get("player") or ""))) or []
            move = next((item for item in movements if item.get("origin") == row.get("team")), None)
            if move and move.get("destination") and move.get("destination") != row.get("team"):
                destination = move["destination"]
                if destination in members:
                    row["player_class"] = "state-transfer-in-conference"
                    row["player_sub"] = f"Transferred → {destination} (in conference)"
                else:
                    destination_conf = team_conferences.get(destination) or "Independent"
                    row["player_class"] = "state-transferred"
                    row["player_sub"] = f"Transferred → {destination} ({destination_conf})"
            else:
                row.pop("player_class", None)
                row.pop("player_sub", None)
            filtered.append(row)

        existing_ids = {str(row.get("player_id") or "") for row in filtered}
        for entry in incoming_by_category.get(category, []):
            if str(entry.get("player_id") or "") in existing_ids:
                continue
            row = {
                **entry["stats"],
                "player": entry["player"],
                "player_id": entry.get("player_id"),
                "position": entry.get("position"),
                "team": entry["destination"],
                "player_class": "state-arrived",
                "player_sub": f"Incoming from {entry['origin']} ({entry['origin_conference']})",
            }
            filtered.append(row)

        statistic = sort_stat(category)
        if statistic:
            filtered.sort(key=lambda row: -float(row.get(statistic) or 0))
        for index, row in enumerate(filtered, start=1):
            row["rank"] = index
        table.rows = filtered
        table.note = (
            f"All qualified {category_label(category).lower()} players · "
            "transfer notes only; incoming transfers include prior-school production"
        )

    return {"leaders": leaders, "groups": groups, "source_season": source_season}
