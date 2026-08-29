"""Consistent, full team-production presentation.

Football readers expect the same category order everywhere.  This layer keeps
that order stable even when a category is populated late (for example by an
incoming transfer), and combines defensive counting categories into one useful
view instead of forcing tackles, interceptions and fumbles into separate tabs.
"""

from __future__ import annotations

from typing import Any

from sports_aggregator.cfb import views
from sports_aggregator.tables import Column, Table


PRIMARY_ORDER = (
    "passing", "rushing", "receiving", "defense",
    "kicking", "punting", "kickReturns", "puntReturns",
)
DEFENSE_CATEGORIES = ("defensive", "interceptions", "fumbles")
STATE_ORDER = {"RETURNING": 0, "ARRIVED": 1, "DEPARTED": 2}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _state_sub(entry: dict[str, Any]) -> str | None:
    label = entry.get("state_label")
    earned = entry.get("earned_at")
    counterpart = entry.get("counterpart")
    state = entry.get("state")
    if not label:
        return None
    text = str(label)
    if earned:
        text += f" from {earned}"
    if state == "DEPARTED" and counterpart:
        text += f" → {counterpart}"
    return text


def _defense_production_group(production: dict[str, Any], season: int) -> dict[str, Any] | None:
    groups = {group.get("category"): group for group in production.get("groups") or []}
    if not any(groups.get(category) for category in DEFENSE_CATEGORIES):
        return None

    players: dict[str, dict[str, Any]] = {}
    for category in DEFENSE_CATEGORIES:
        group = groups.get(category) or {}
        for entry in group.get("players") or []:
            player_id = str(entry.get("player_id") or entry.get("player") or "")
            if not player_id:
                continue
            item = players.setdefault(player_id, {
                "entry": entry,
                "categories": {},
            })
            item["categories"][category] = dict(entry.get("stats") or {})
            # Prefer the defensive row as the identity/state source because it
            # represents the broadest sample, but never discard an INT-only row.
            if category == "defensive":
                item["entry"] = entry

    rows = []
    for player_id, packet in players.items():
        entry = packet["entry"]
        defensive = packet["categories"].get("defensive") or {}
        interceptions = packet["categories"].get("interceptions") or {}
        fumbles = packet["categories"].get("fumbles") or {}
        row = {
            "player": entry.get("player"),
            "player_url": views._player_url(entry.get("player_id"), season),
            "player_sub": _state_sub(entry),
            "player_class": f"state-{str(entry.get('state') or '').lower()}" if entry.get("state") else None,
            "state": entry.get("state_label"),
            "state_class": f"state-{str(entry.get('state') or '').lower()}" if entry.get("state") else None,
            "position": entry.get("position"),
            "SOLO": defensive.get("SOLO"),
            "TOT": defensive.get("TOT"),
            "TFL": defensive.get("TFL"),
            "SACKS": defensive.get("SACKS"),
            "QB HUR": defensive.get("QB HUR"),
            "PD": defensive.get("PD"),
            "INT": interceptions.get("INT"),
            "FUM": fumbles.get("FUM"),
            "FR": fumbles.get("REC"),
            "_state": entry.get("state"),
        }
        rows.append(row)

    # The default question on a preseason team page is "what do we have?".
    # Keep current-roster production first, while sortable stat headers still
    # let the reader rank the entire set by tackles, sacks, interceptions, etc.
    rows.sort(key=lambda row: (
        STATE_ORDER.get(str(row.get("_state") or ""), 9),
        -_number(row.get("TOT")), -_number(row.get("TFL")),
        -_number(row.get("SACKS")), -_number(row.get("INT")),
        str(row.get("player") or ""),
    ))
    for row in rows:
        row.pop("_state", None)

    counts = {state: 0 for state in STATE_ORDER}
    for packet in players.values():
        state = packet["entry"].get("state")
        if state in counts:
            counts[state] += 1
    note = (f"{counts['RETURNING']} returning · {counts['ARRIVED']} arrived · "
            f"{counts['DEPARTED']} departed · tackles + takeaways combined")
    table = Table(
        columns=[
            Column(key="player", label="Player", align="left", emphasis=True),
            Column(key="state", label="Status", align="left"),
            Column(key="position", label="Pos", align="left"),
            Column(key="SOLO", label="Solo", format="int"),
            Column(key="TOT", label="Tkl", format="int"),
            Column(key="TFL", label="TFL", format="f1"),
            Column(key="SACKS", label="Sack", format="f1"),
            Column(key="QB HUR", label="Hur", format="int"),
            Column(key="PD", label="PD", format="int"),
            Column(key="INT", label="INT", format="int"),
            Column(key="FUM", label="Fum", format="int"),
            Column(key="FR", label="FR", format="int"),
        ],
        rows=rows,
        caption="Defense",
        note=note,
        empty="No defensive production is stored.",
    )
    return {"category": "defense", "label": "Defense", "table": table}


def _ordered_production_groups(original, production, season):
    raw_groups = production.get("groups") or []
    rendered = original(production, season)
    by_category = {
        raw.get("category"): rendered[index]
        for index, raw in enumerate(raw_groups)
        if index < len(rendered)
    }
    # Current players first within every non-defensive category too. Stable sort
    # preserves the existing production ranking inside each state.
    for category, item in by_category.items():
        if category in DEFENSE_CATEGORIES:
            continue
        item["category"] = category
        item["table"].rows.sort(key=lambda row: {
            "Returning": 0, "Arrived": 1, "Departed": 2
        }.get(str(row.get("state") or ""), 9))

    defense = _defense_production_group(production, season)
    result = []
    for category in PRIMARY_ORDER:
        if category == "defense":
            if defense:
                result.append(defense)
            continue
        item = by_category.get(category)
        if item:
            result.append(item)
    # Preserve any future CFBD categories after the conventional football set.
    used = set(PRIMARY_ORDER) | set(DEFENSE_CATEGORIES)
    result.extend(item for category, item in by_category.items() if category not in used)
    return result


def _defense_leaders(leaders: dict[str, Any], season: int,
                     include_team: bool) -> dict[str, Any] | None:
    groups = leaders.get("groups") or {}
    if not any(groups.get(category) for category in DEFENSE_CATEGORIES):
        return None
    players: dict[str, dict[str, Any]] = {}
    for category in DEFENSE_CATEGORIES:
        group = groups.get(category) or {}
        for entry in group.get("players") or []:
            player_id = str(entry.get("player_id") or entry.get("player") or "")
            if not player_id:
                continue
            item = players.setdefault(player_id, {"entry": entry, "categories": {}})
            item["categories"][category] = dict(entry.get("stats") or {})
            if category == "defensive":
                item["entry"] = entry

    rows = []
    for packet in players.values():
        entry = packet["entry"]
        defense = packet["categories"].get("defensive") or {}
        ints = packet["categories"].get("interceptions") or {}
        fumbles = packet["categories"].get("fumbles") or {}
        row = {
            "player": entry.get("player"),
            "player_url": views._player_url(entry.get("player_id"), season),
            "player_sub": f"arrived from {entry.get('origin')}" if entry.get("arrival") else None,
            "player_class": "state-arrived" if entry.get("arrival") else None,
            "position": entry.get("position"),
            "team": entry.get("team"),
            "TOT": defense.get("TOT"), "TFL": defense.get("TFL"),
            "SACKS": defense.get("SACKS"), "QB HUR": defense.get("QB HUR"),
            "PD": defense.get("PD"), "INT": ints.get("INT"),
            "FUM": fumbles.get("FUM"), "FR": fumbles.get("REC"),
        }
        rows.append(row)
    rows.sort(key=lambda row: (
        -_number(row.get("TOT")), -_number(row.get("TFL")),
        -_number(row.get("SACKS")), -_number(row.get("INT")),
        str(row.get("player") or ""),
    ))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    columns = [
        Column(key="rank", label="#", format="rank", align="right"),
        Column(key="player", label="Player", align="left", emphasis=True),
        Column(key="position", label="Pos", align="left"),
    ]
    if include_team:
        columns.append(Column(key="team", label="Team", align="left"))
    columns.extend([
        Column(key="TOT", label="Tkl", format="int"),
        Column(key="TFL", label="TFL", format="f1"),
        Column(key="SACKS", label="Sack", format="f1"),
        Column(key="QB HUR", label="Hur", format="int"),
        Column(key="PD", label="PD", format="int"),
        Column(key="INT", label="INT", format="int"),
        Column(key="FUM", label="Fum", format="int"),
        Column(key="FR", label="FR", format="int"),
    ])
    return {
        "category": "defense", "label": "Defense",
        "table": Table(columns=columns, rows=rows, caption="Defense",
                       note="tackles, pressure and takeaways combined",
                       empty="No defensive leaders are stored."),
    }


def _ordered_leader_groups(original, leaders, season, *, include_team=True, limit=None):
    rendered = original(leaders, season, include_team=include_team, limit=limit)
    by_category = {item.get("category"): item for item in rendered}
    defense = _defense_leaders(leaders, season, include_team)
    result = []
    for category in PRIMARY_ORDER:
        if category == "defense":
            if defense:
                result.append(defense)
            continue
        item = by_category.get(category)
        if item:
            result.append(item)
    used = set(PRIMARY_ORDER) | set(DEFENSE_CATEGORIES)
    result.extend(item for category, item in by_category.items() if category not in used)
    return result


def install_production_display(app) -> None:
    if app.extensions.get("production_display_installed"):
        return
    original_production = views.production_groups
    original_leaders = views.leader_groups

    def production_groups(production, season):
        return _ordered_production_groups(original_production, production, season)

    def leader_groups(leaders, season, *, include_team=True, limit=None):
        return _ordered_leader_groups(
            original_leaders, leaders, season, include_team=include_team, limit=limit)

    views.production_groups = production_groups
    views.leader_groups = leader_groups
    app.extensions["production_display_installed"] = True
