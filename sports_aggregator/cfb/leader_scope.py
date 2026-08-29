"""Keep team-specific production packets tied to the requested roster.

Preseason leaderboards deliberately mix prior-season production with current
roster context. A transferred-in player's stat row therefore carries the school
where those stats were earned, which is useful provenance but is not the scope
of the panel. This guard validates every team-specific packet against the
requested season's roster and normalizes the internal team field to the current
school while preserving transfer origin metadata.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any


def sanitize_team_leader_packet(packet: dict[str, Any], *, team: str,
                                active_ids: set[str]) -> dict[str, Any]:
    """Drop rows that do not belong to the requested current-season roster."""
    groups = packet.get("groups") or {}
    for group in groups.values():
        players = []
        for raw in group.get("players") or []:
            player_id = str(raw.get("player_id") or "")
            if active_ids and player_id and player_id not in active_ids:
                continue
            entry = dict(raw)
            # `origin` remains the source of an arrival's historical production.
            # `team` is the panel scope and must not redirect later expansion to
            # the school where that production happened.
            entry["team"] = team
            players.append(entry)
        group["players"] = players
    packet["team_scope"] = team
    return packet


def install_team_leader_scope_guard(repository_class) -> None:
    """Wrap ``team_player_leaders`` once for all repository instances."""
    if getattr(repository_class, "_team_leader_scope_guard", False):
        return

    original = repository_class.team_player_leaders

    def team_player_leaders(self, team: str, season: int, limit: int = 8):
        packet = original(self, team, season, limit)
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT player_id FROM players WHERE season=? AND team=?",
                (int(season), str(team)),
            ).fetchall()
        active_ids = {str(row[0]) for row in rows if row[0] is not None}
        # If the requested roster has not been synced yet, preserve the original
        # packet rather than turning a data-availability issue into an empty UI.
        if not active_ids:
            packet["team_scope"] = team
            return packet
        return sanitize_team_leader_packet(packet, team=team, active_ids=active_ids)

    repository_class.team_player_leaders = team_player_leaders
    repository_class._team_leader_scope_guard = True
