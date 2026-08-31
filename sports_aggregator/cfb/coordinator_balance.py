"""Observed run/pass balance for offensive-coordinator assignments.

This is descriptive team offense data, not a claim that the coordinator called
every play. Splits are tied to the seasons and programs where the coordinator is
stored as the offensive coordinator.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.coordinators import initialize


RUSH_KEYS = {
    "rushingattempts", "rushattempts", "rushingatt", "rushatt", "carries"
}
PASS_KEYS = {
    "passingattempts", "passattempts", "passingatt", "passatt", "passesattempted"
}


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _season_split(connection, season: int, team: str) -> dict[str, Any] | None:
    rows = connection.execute(
        "SELECT stat_name,stat_value FROM team_stats WHERE season=? AND team=?",
        (int(season), str(team)),
    ).fetchall()
    rush = pas = None
    for row in rows:
        key = _norm(row["stat_name"])
        if key in RUSH_KEYS:
            rush = _number(row["stat_value"])
        elif key in PASS_KEYS:
            pas = _number(row["stat_value"])
    if rush is None or pas is None or rush + pas <= 0:
        return None
    total = rush + pas
    return {
        "season": int(season),
        "team": str(team),
        "rush_attempts": int(rush),
        "pass_attempts": int(pas),
        "plays": int(total),
        "run_pct": round(100 * rush / total, 1),
        "pass_pct": round(100 * pas / total, 1),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    rush = sum(float(row["rush_attempts"]) for row in rows)
    pas = sum(float(row["pass_attempts"]) for row in rows)
    total = rush + pas
    if total <= 0:
        return None
    return {
        "rush_attempts": int(rush),
        "pass_attempts": int(pas),
        "plays": int(total),
        "run_pct": round(100 * rush / total, 1),
        "pass_pct": round(100 * pas / total, 1),
        "seasons": len(rows),
    }


def coordinator_run_pass_context(repository, team_id: int, season: int) -> dict[str, Any] | None:
    initialize(repository)
    with repository._reader() as connection:
        current = connection.execute(
            """SELECT season,team_id,team,role,coach_name
               FROM coordinator_seasons
               WHERE team_id=? AND season=? AND side='offense' LIMIT 1""",
            (int(team_id), int(season)),
        ).fetchone()
        if current is None:
            return None
        current = dict(current)
        assignments = [dict(row) for row in connection.execute(
            """SELECT season,team_id,team,role,coach_name
               FROM coordinator_seasons
               WHERE coach_name=? AND side='offense' AND season<=?
               ORDER BY season,team_id""",
            (current["coach_name"], int(season)),
        ).fetchall()]
        splits = []
        for assignment in assignments:
            split = _season_split(connection, assignment["season"], assignment["team"])
            if split:
                splits.append(split)

    program = [row for row in splits if row["team"] == current["team"]]
    current_split = next(
        (row for row in splits if row["season"] == int(season) and row["team"] == current["team"]),
        None,
    )
    return {
        "coach_name": current["coach_name"],
        "role": current["role"],
        "team": current["team"],
        "season": int(season),
        "current": current_split,
        "program": _aggregate(program),
        "career": _aggregate(splits),
        "season_splits": sorted(splits, key=lambda row: (row["season"], row["team"]), reverse=True),
    }
