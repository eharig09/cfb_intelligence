"""Blend completed-game role evidence into the projected depth-chart display."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sports_aggregator.cfb import views
from sports_aggregator.cfb.depth_chart_observed import observed_depth_roles
from sports_aggregator.tables import Column


def _role_label(observation: dict[str, Any] | None, rank: int | None) -> str | None:
    if not observation:
        return None
    games = int(observation.get("observed_games") or 0)
    if games <= 0:
        return None
    week = observation.get("latest_week")
    pieces = [f"Observed #{rank}" if rank else "Observed"]
    pieces.append(f"{games} game" if games == 1 else f"{games} games")
    if week is not None:
        pieces.append(f"through Wk {week}")
    return " · ".join(pieces)


def _prepare_depth(depth_chart: dict[str, Any], observations: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    """Reorder mature observed roles and retain row-level labels for the renderer."""
    prepared = deepcopy(depth_chart)
    row_evidence: dict[tuple[str, str], dict[str, Any]] = {}

    for _unit, groups in (prepared.get("units") or {}).items():
        for group, players in groups.items():
            original_order = {str(player.get("player_id")): index for index, player in enumerate(players)}
            scored = []
            for player in players:
                player_id = str(player.get("player_id") or "")
                observation = observations.get(player_id)
                scored.append((player, observation))

            # Rank everyone with actual evidence for display, even during the
            # first game. This lets the page show what happened immediately.
            observed_only = sorted(
                ((player, obs) for player, obs in scored if obs),
                key=lambda item: -float(item[1].get("observed_score") or 0),
            )
            observed_rank = {
                str(player.get("player_id")): index + 1
                for index, (player, _obs) in enumerate(observed_only)
            }

            # Reorder only after repeated evidence. One-game observations are
            # visible, but the published/projection order stays intact until a
            # role has appeared in at least two games.
            def sort_key(item):
                player, obs = item
                player_id = str(player.get("player_id") or "")
                original = original_order.get(player_id, 999)
                if obs and obs.get("can_reorder"):
                    return (0, -float(obs.get("observed_score") or 0), original)
                return (1, original, original)

            scored.sort(key=sort_key)
            groups[group] = [player for player, _obs in scored]

            for player, obs in scored:
                player_id = str(player.get("player_id") or "")
                if not player_id:
                    continue
                rank = observed_rank.get(player_id)
                row_evidence[(str(group), player_id)] = {
                    "role": _role_label(obs, rank),
                    "role_sort": rank,
                    "confidence": (obs or {}).get("confidence"),
                }
    return prepared, row_evidence


def install_observed_depth_display(repository) -> None:
    """Wrap the already-installed depth renderer with in-season evidence."""
    if getattr(views, "_observed_depth_display_installed", False):
        return
    base_renderer = views.depth_chart_tables

    def rendered(depth_chart: dict[str, Any], season: int,
                 projection: dict[str, dict[str, Any]] | None = None):
        team = str(depth_chart.get("team") or "")
        observations = observed_depth_roles(repository, team, int(season)) if team else {}
        prepared, row_evidence = _prepare_depth(depth_chart, observations)
        units = base_renderer(prepared, season, projection)

        source_units = prepared.get("units") or {}
        for unit_packet in units:
            unit_name = unit_packet.get("unit")
            source_groups = source_units.get(unit_name) or {}
            for group_packet in unit_packet.get("groups") or []:
                table = group_packet.get("table")
                group_label = str(group_packet.get("label") or "")
                # Labels are rendered as "Quarterback (4)"; recover the group
                # key by matching the prefix against the prepared packet.
                group_name = next(
                    (name for name in source_groups if group_label.startswith(f"{name} (")),
                    None,
                )
                if table is None or group_name is None:
                    continue
                players = source_groups.get(group_name) or []
                by_name = {
                    str(player.get("name") or ""): str(player.get("player_id") or "")
                    for player in players
                }
                any_role = False
                for row in table.rows:
                    player_id = by_name.get(str(row.get("name") or ""), "")
                    evidence = row_evidence.get((str(group_name), player_id)) or {}
                    role = evidence.get("role")
                    row["observed_role"] = role
                    row["observed_role_sort"] = evidence.get("role_sort")
                    if role:
                        confidence = evidence.get("confidence")
                        row["observed_role_sub"] = (
                            f"{confidence} confidence from box-score usage" if confidence else
                            "box-score usage"
                        )
                        any_role = True
                if any_role:
                    columns = list(table.columns)
                    insert_at = 2 if len(columns) >= 2 else len(columns)
                    columns.insert(
                        insert_at,
                        Column(
                            key="observed_role",
                            label="Game role",
                            align="left",
                            sort="number",
                            title=("Observed role from recent completed-game box scores. "
                                   "One-game evidence is shown immediately; ordering changes "
                                   "after repeated usage. Positions without box-score usage "
                                   "remain projection-led."),
                        ),
                    )
                    table.columns = columns
                    note = table.note or ""
                    live_note = (
                        "Game role uses current-roster players only. Box-score evidence can "
                        "update skill/defensive roles; OL and other low-stat positions remain "
                        "projection-led until snap/start evidence is available."
                    )
                    table.note = f"{note} · {live_note}" if note else live_note
        return units

    views.depth_chart_tables = rendered
    views._observed_depth_display_installed = True
