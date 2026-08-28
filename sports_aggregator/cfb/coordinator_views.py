"""Presentation-ready coordinator tables for team and matchup pages."""

from __future__ import annotations

from typing import Any

from sports_aggregator.cfb.coordinator_context import (
    coordinator_context,
    coordinator_matchup_context,
)
from sports_aggregator.tables import Column, Table


def _side_row(item: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if not item:
        return {
            "unit": label,
            "coordinator": "—",
            "continuity": "Not available",
            "previous": "—",
            "previous_stop": "—",
            "source": None,
        }
    previous_stop = item.get("previous_stop") or {}
    return {
        "unit": label,
        "coordinator": item.get("coach_name") or "—",
        "continuity": item.get("continuity_label") or "—",
        "previous": item.get("previous_coordinator") or "—",
        "previous_stop": previous_stop.get("team") or "—",
        "source": item.get("official_source_url") or item.get("source_url"),
    }


def team_coordinator_table(repository, team_id: int, season: int) -> Table:
    context = coordinator_context(repository, int(team_id), int(season))
    rows = [
        _side_row(context.get("offense"), "Offense"),
        _side_row(context.get("defense"), "Defense"),
    ]
    return Table(
        columns=[
            Column("unit", "Unit"),
            Column("coordinator", "Coordinator", emphasis=True),
            Column("continuity", "Continuity"),
            Column("previous", "Previous coordinator"),
            Column("previous_stop", "Previous stop"),
        ],
        rows=rows,
        caption="Coordinator continuity",
        note=(
            "Continuity is derived only from stored adjacent seasons. Missing prior-year data is "
            "reported as unknown rather than inferred as a staff change."
        ),
        dense=True,
        empty="Coordinator data is not yet available for this team.",
    )


def matchup_coordinator_table(repository, away_team_id: int, home_team_id: int,
                              away_team: str, home_team: str, season: int) -> Table:
    context = coordinator_matchup_context(
        repository, int(away_team_id), int(home_team_id), int(season)
    )
    rows = []
    for location, team_name in (("away", away_team), ("home", home_team)):
        team_context = context.get(location) or {}
        for side, unit in (("offense", "OC"), ("defense", "DC")):
            item = team_context.get(side)
            rows.append({
                "team": team_name,
                "unit": unit,
                "coordinator": (item or {}).get("coach_name") or "—",
                "continuity": (item or {}).get("continuity_label") or "Not available",
                "changed": (
                    "New" if (item or {}).get("changed") is True
                    else "Returning" if (item or {}).get("changed") is False
                    else "Unknown"
                ),
            })
    return Table(
        columns=[
            Column("team", "Team", emphasis=True),
            Column("unit", "Role"),
            Column("coordinator", "Coordinator"),
            Column("continuity", "Continuity"),
            Column("changed", "This offseason"),
        ],
        rows=rows,
        caption="Coordinator comparison",
        note=(
            "A change is only called when both the current and immediately previous season are stored."
        ),
        dense=True,
        empty="Coordinator comparison is not yet available.",
    )
