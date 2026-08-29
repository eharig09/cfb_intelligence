"""Where a team's production actually stands before a snap is played.

In preseason a team page shows last season's leaders, which quietly misleads:
some of those players are gone, and the transfers who replace them produced
somewhere else entirely. The honest preseason question is not "who led this team
last year" but "what production is here, what left, and what arrived".

So every graded player is placed in one of three states:

* **Returning** -- on both rosters. His numbers carry over unqualified.
* **Arrived** -- on this roster now, produced elsewhere last season. His numbers
  are real but were earned at another school, which is always labeled.
* **Departed** -- on last season's roster, not on this one. His numbers are what
  the team has to replace.

Arrived production is never silently added to a team total. It is shown beside
returning production with its origin attached, because the two are not the same
kind of evidence.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.statlines import CATEGORY_ORDER, category_label, sort_stat


#: Display states, in the order a reader should meet them.
RETURNING, ARRIVED, DEPARTED = "RETURNING", "ARRIVED", "DEPARTED"

STATE_LABELS = {
    RETURNING: "Returning",
    ARRIVED: "Arrived",
    DEPARTED: "Departed",
}

#: A player must clear the sort statistic by this much to be worth listing.
MIN_HEADLINE_VALUE = 1.0


def _stat_lines(connection, player_ids: list[str], season: int) -> dict[str, dict[str, Any]]:
    """Pivoted stat lines for specific players in one season."""
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = connection.execute(
        f"""SELECT player_id,player,team,position,category,stat_type,numeric_value,stat_value
            FROM player_season_stats WHERE season=? AND player_id IN ({placeholders})""",
        (season, *player_ids)).fetchall()
    lines: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = (row["player_id"], row["category"])
        entry = lines.setdefault(key, {
            "player_id": row["player_id"], "player": row["player"],
            "team": row["team"], "position": row["position"],
            "category": row["category"], "stats": {},
        })
        value = row["numeric_value"]
        entry["stats"][row["stat_type"]] = value if value is not None else row["stat_value"]
    return lines


def _stats_by_player(connection, player_ids: list[str], season: int) -> dict[str, dict[str, Any]]:
    """All prior-season counting lines for each player, grouped by category."""
    grouped: dict[str, dict[str, Any]] = {}
    for (player_id, category), line in _stat_lines(connection, player_ids, season).items():
        player = grouped.setdefault(player_id, {"team": line.get("team"), "categories": {}})
        player["categories"][category] = dict(line.get("stats") or {})
    return grouped


def _pff_by_player(connection, season: int) -> dict[str, dict[str, Any]]:
    """Dataset-specific PFF grades for players already linked to CFBD identities."""
    rows = connection.execute(
        """SELECT p.cfbd_player_id,p.interest_score,p.cfbd_team,
                  m.dataset,m.primary_grade,m.usage_count,m.game_count,m.metrics_json
           FROM pff_players p
           LEFT JOIN pff_player_metrics m
             ON m.season=p.season AND m.pff_player_id=p.pff_player_id
           WHERE p.season=? AND p.cfbd_player_id IS NOT NULL""",
        (season,),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        player_id = str(row["cfbd_player_id"])
        item = result.setdefault(player_id, {
            "interest_score": row["interest_score"],
            "team": row["cfbd_team"],
            "datasets": {},
        })
        if row["dataset"]:
            item["datasets"][str(row["dataset"])] = {
                "primary_grade": row["primary_grade"],
                "usage_count": row["usage_count"],
                "game_count": row["game_count"],
                "metrics_json": row["metrics_json"],
            }
    return result


def team_production(repository: CFBRepository, team_id: int, season: int, *,
                    stat_season: int | None = None, per_category: int = 6) -> dict[str, Any]:
    """Returning, arrived and departed production for one team."""
    team = repository.get_team(team_id)
    if team is None:
        return {"season": season, "stat_season": None, "groups": [], "totals": {}}
    stat_season = stat_season or (season - 1)
    movements = repository.roster_movements(team_id, season)
    arrivals = {row["player_id"]: row for row in movements["arrivals"] if row.get("player_id")}
    departures = {row["player_id"]: row for row in movements["departures"] if row.get("player_id")}

    with closing(repository._connect()) as connection:
        current = [dict(row) for row in connection.execute(
            "SELECT player_id,first_name,last_name,position FROM players "
            "WHERE season=? AND team=?", (season, team["school"]))]
        identifiers = [row["player_id"] for row in current] + list(departures)
        lines = _stat_lines(connection, identifiers, stat_season)

    by_category: dict[str, list[dict[str, Any]]] = {}
    for (player_id, category), entry in lines.items():
        statistic = sort_stat(category)
        if not statistic:
            continue
        headline = entry["stats"].get(statistic)
        try:
            headline_value = float(headline)
        except (TypeError, ValueError):
            continue
        if headline_value < MIN_HEADLINE_VALUE:
            continue
        if player_id in departures:
            movement = departures[player_id]
            state = DEPARTED
            note = (movement.get("movement_type") or "").replace("_", " ").title()
            counterpart = movement.get("destination")
        elif player_id in arrivals:
            movement = arrivals[player_id]
            state = ARRIVED
            note = (movement.get("movement_type") or "").replace("_", " ").title()
            counterpart = movement.get("origin") or entry["team"]
        else:
            state, note, counterpart = RETURNING, "Returning", None
        earned_at = entry["team"] if entry["team"] != team["school"] else None
        by_category.setdefault(category, []).append({
            **entry,
            "state": state,
            "state_label": STATE_LABELS[state],
            "note": note,
            "counterpart": counterpart,
            "earned_at": earned_at,
            "headline_stat": statistic,
            "headline_value": headline_value,
        })

    groups = []
    totals = {RETURNING: 0.0, ARRIVED: 0.0, DEPARTED: 0.0}
    for category in CATEGORY_ORDER:
        entries = by_category.get(category)
        if not entries:
            continue
        entries.sort(key=lambda row: -row["headline_value"])
        for entry in entries:
            totals[entry["state"]] += entry["headline_value"]
        groups.append({
            "category": category,
            "label": category_label(category),
            "statistic": sort_stat(category),
            "players": entries[:per_category],
            "counts": {
                state: sum(1 for row in entries if row["state"] == state)
                for state in (RETURNING, ARRIVED, DEPARTED)
            },
        })
    share = None
    kept = totals[RETURNING] + totals[ARRIVED]
    if kept + totals[DEPARTED] > 0:
        share = round(100 * kept / (kept + totals[DEPARTED]), 1)
    return {
        "season": season, "stat_season": stat_season, "team": team["school"],
        "groups": groups,
        "totals": {state: round(value, 1) for state, value in totals.items()},
        "retained_share": share,
    }


def projected_depth(repository: CFBRepository, team_id: int, season: int, *,
                    stat_season: int | None = None) -> dict[str, dict[str, Any]]:
    """Production and grade evidence per current player for the depth board.

    Ordering still follows the strongest headline production / grade evidence,
    but the packet now carries every prior-season stat category and every linked
    PFF dataset so the UI can show a real position-specific profile instead of
    one headline number followed by a duplicate PFF score.
    """
    stat_season = stat_season or (season - 1)
    production = team_production(repository, team_id, season, stat_season=stat_season,
                                 per_category=200)
    team = repository.get_team(team_id)
    if team is None:
        return {}

    with closing(repository._connect()) as connection:
        current_rows = [dict(row) for row in connection.execute(
            "SELECT player_id FROM players WHERE season=? AND team=?",
            (season, team["school"]),
        ).fetchall()]
        current_ids = [str(row["player_id"]) for row in current_rows]
        full_stats = _stats_by_player(connection, current_ids, stat_season)
        grades = _pff_by_player(connection, stat_season)

    evidence: dict[str, dict[str, Any]] = {}
    for group in production["groups"]:
        for entry in group["players"]:
            if entry["state"] == DEPARTED:
                continue
            current = evidence.get(entry["player_id"])
            if current and current["headline_value"] >= entry["headline_value"]:
                continue
            grade = grades.get(entry["player_id"]) or {}
            stat_packet = full_stats.get(entry["player_id"]) or {}
            evidence[entry["player_id"]] = {
                "state": entry["state"],
                "category": entry["category"],
                "headline_stat": entry["headline_stat"],
                "headline_value": entry["headline_value"],
                "earned_at": entry["earned_at"],
                "grade": grade.get("interest_score"),
                "graded_at": grade.get("team"),
                "stats_by_category": stat_packet.get("categories") or {},
                "stats_team": stat_packet.get("team"),
                "pff_datasets": grade.get("datasets") or {},
                "stat_season": stat_season,
            }

    # Players with grades or lower-volume stats still need a profile even when
    # they never cleared the leaderboard threshold used for depth ordering.
    for player_id in current_ids:
        if player_id in evidence:
            continue
        grade = grades.get(player_id) or {}
        stat_packet = full_stats.get(player_id) or {}
        if not grade and not stat_packet:
            continue
        evidence[player_id] = {
            "state": None,
            "category": None,
            "headline_stat": None,
            "headline_value": 0.0,
            "earned_at": (stat_packet.get("team") if stat_packet.get("team") != team["school"] else None),
            "grade": grade.get("interest_score"),
            "graded_at": grade.get("team"),
            "stats_by_category": stat_packet.get("categories") or {},
            "stats_team": stat_packet.get("team"),
            "pff_datasets": grade.get("datasets") or {},
            "stat_season": stat_season,
        }
    return evidence
