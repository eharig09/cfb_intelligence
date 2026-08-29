"""Trusted player-page career context.

This deliberately avoids diagnosing injuries. It adds only:
- original CFBD recruiting pedigree, matched by athlete id when possible;
- distinct stored game appearances by season;
- a boolean that sourced injury-reporting rows exist for that season.

Transfer ratings remain separate portal data and are never substituted for the
player's original recruiting rating.
"""

from __future__ import annotations

from contextlib import closing
import sqlite3
from typing import Any

from sports_aggregator.cfb.models import normalize_alias


def _recruit_for_player(connection, player: dict[str, Any]) -> dict[str, Any] | None:
    player_id = str(player.get("player_id") or "").strip()
    if player_id:
        row = connection.execute(
            """SELECT * FROM recruits
               WHERE athlete_id=?
               ORDER BY season ASC LIMIT 1""",
            (player_id,),
        ).fetchone()
        if row:
            return dict(row)

    normalized = normalize_alias(str(player.get("name") or ""))
    if not normalized:
        return None
    career_teams = {
        str(stint.get("team") or "").strip()
        for stint in (player.get("stints") or [])
        if str(stint.get("team") or "").strip()
    }
    params: list[Any] = [normalized]
    sql = "SELECT * FROM recruits WHERE normalized_name=?"
    if career_teams:
        placeholders = ",".join("?" for _ in career_teams)
        sql += f" AND committed_to IN ({placeholders})"
        params.extend(sorted(career_teams))
    rows = [dict(row) for row in connection.execute(sql + " ORDER BY season ASC", params)]
    # Name fallback is only safe when it resolves to one recruiting record.
    return rows[0] if len(rows) == 1 else None


def _games_by_season(connection, player_id: str) -> dict[int, int]:
    rows = connection.execute(
        """SELECT g.season,COUNT(DISTINCT b.game_id) games
           FROM game_player_box_stats b
           JOIN games g ON g.game_id=b.game_id
           WHERE b.player_id=?
           GROUP BY g.season""",
        (str(player_id),),
    ).fetchall()
    return {int(row["season"]): int(row["games"] or 0) for row in rows}


def _injury_presence(connection, player_id: str) -> set[int]:
    try:
        exists = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='player_injury_events'"""
        ).fetchone()
        if not exists:
            return set()
        return {
            int(row["season"])
            for row in connection.execute(
                """SELECT DISTINCT season FROM player_injury_events
                   WHERE player_id=?""",
                (str(player_id),),
            )
            if row["season"] is not None
        }
    except sqlite3.Error:
        return set()


def player_career_context(repository, player: dict[str, Any]) -> dict[str, Any]:
    repository.initialize()
    player_id = str(player.get("player_id") or "")
    with closing(repository._connect()) as connection:
        recruit = _recruit_for_player(connection, player)
        games = _games_by_season(connection, player_id)
        injury_seasons = _injury_presence(connection, player_id)

    stints = []
    for raw in player.get("stints") or []:
        stint = dict(raw)
        year = int(stint["season"])
        stint["games_recorded"] = games.get(year)
        stint["injury_data_present"] = year in injury_seasons
        stints.append(stint)

    return {
        "recruit": recruit,
        "stints": stints,
        "has_prior_college_season": len({row["season"] for row in stints}) > 1,
    }
