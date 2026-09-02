"""Consistent, full team-production presentation.

Football readers expect the same category order everywhere. This layer keeps
that order stable, combines defensive counting categories, expands matchup
leader tables to the full qualifying team pool, and adds selected PFF signature
metrics only when the imported raw PFF rows actually contain them.
"""

from __future__ import annotations

from contextlib import closing
import json
from typing import Any

from sports_aggregator.cfb import views
from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.tables import Column, Table


PRIMARY_ORDER = (
    "passing", "rushing", "receiving", "defense",
    "kicking", "punting", "kickReturns", "puntReturns",
)
DEFENSE_CATEGORIES = ("defensive", "interceptions", "fumbles")
STATE_ORDER = {"RETURNING": 0, "ARRIVED": 1, "DEPARTED": 2}
FULL_TEAM_LEADER_LIMIT = 200


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


def _interest_for(entry: dict[str, Any], interest: dict[str, Any]):
    """The same two-way lookup the view uses: by roster id, then by name."""
    return (interest.get(str(entry.get("player_id") or ""))
            or interest.get(normalize_alias(entry.get("player") or "")))


def _state_class(entry: dict[str, Any], interest: dict[str, Any]) -> str | None:
    state = str(entry.get("state") or "")
    if not state:
        return None
    graded = state == "RETURNING" and _interest_for(entry, interest) is not None
    return f"state-{state.lower()}" + (" state-graded" if graded else "")


def _defense_production_group(production: dict[str, Any], season: int,
                              interest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Tackles and takeaways from three categories, folded into one table.

    Because it rebuilds its rows rather than decorating the view's, every
    per-row signal the view adds has to be added again here or it reaches
    only offensive players -- which is how the graded-returner mark came out
    invisible on a team whose graded returners are all defenders.
    """
    interest = interest or {}
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
            item = players.setdefault(player_id, {"entry": entry, "categories": {}})
            item["categories"][category] = dict(entry.get("stats") or {})
            if category == "defensive":
                item["entry"] = entry

    rows = []
    for player_id, packet in players.items():
        entry = packet["entry"]
        defensive = packet["categories"].get("defensive") or {}
        interceptions = packet["categories"].get("interceptions") or {}
        fumbles = packet["categories"].get("fumbles") or {}
        rows.append({
            "player_id": entry.get("player_id"),
            "player": entry.get("player"),
            "player_url": views._player_url(entry.get("player_id"), season),
            "player_sub": _state_sub(entry),
            "player_class": _state_class(entry, interest),
            "interest": _interest_for(entry, interest),
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
        })

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
    return {
        "category": "defense", "label": "Defense",
        "table": Table(
            columns=[
                Column(key="player", label="Player", align="left", emphasis=True),
                Column(key="state", label="Status", align="left"),
                Column(key="position", label="Pos", align="left"),
                Column(key="interest", label="PFF", format="f1",
                       title="Application interest score from the 2025 PFF "
                             "snapshot; discounts small samples. Present only "
                             "for players it graded."),
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
            rows=rows, caption="Defense", note=note,
            empty="No defensive production is stored.",
        ),
    }


def _ordered_production_groups(original, production, season, **kwargs):
    raw_groups = production.get("groups") or []
    rendered = original(production, season, **kwargs)
    by_category = {
        raw.get("category"): rendered[index]
        for index, raw in enumerate(raw_groups)
        if index < len(rendered)
    }
    for category, item in by_category.items():
        if category in DEFENSE_CATEGORIES:
            continue
        item["category"] = category
        item["table"].rows.sort(key=lambda row: {
            "Returning": 0, "Arrived": 1, "Departed": 2
        }.get(str(row.get("state") or ""), 9))

    defense = _defense_production_group(production, season, kwargs.get("interest"))
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
        rows.append({
            "player_id": entry.get("player_id"),
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
        })
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


def _first_team(leaders: dict[str, Any]) -> str | None:
    for group in (leaders.get("groups") or {}).values():
        for player in group.get("players") or []:
            if player.get("team"):
                return str(player["team"])
    return None


def _full_team_leaders(repository, leaders: dict[str, Any], season: int,
                       include_team: bool) -> dict[str, Any]:
    """Expand team-v-team leader packets beyond the repository's compact default."""
    if include_team:
        return leaders
    team = _first_team(leaders)
    if not team:
        return leaders
    return repository.team_player_leaders(team, season, limit=FULL_TEAM_LEADER_LIMIT)


def _raw_metrics_by_player(repository, player_ids: list[str], pff_season: int) -> dict[str, dict[str, dict[str, Any]]]:
    ids = list(dict.fromkeys(str(player_id) for player_id in player_ids if player_id))
    if not ids:
        return {}
    # Every other reader in the repository does this first, and this one is the
    # only path to the PFF tables from a page: without it a database that has
    # not been initialized in this process 500s the conference page on a
    # missing table rather than showing it without grades.
    repository.initialize()
    placeholders = ",".join("?" for _ in ids)
    with repository._reader() as connection:
        rows = connection.execute(
            f"""SELECT p.cfbd_player_id,m.dataset,m.metrics_json
                FROM pff_players p JOIN pff_player_metrics m
                  ON m.season=p.season AND m.pff_player_id=p.pff_player_id
                WHERE p.season=? AND p.cfbd_player_id IN ({placeholders})""",
            (int(pff_season), *ids),
        ).fetchall()
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        try:
            raw = json.loads(row["metrics_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            raw = {}
        result.setdefault(str(row["cfbd_player_id"]), {})[str(row["dataset"])] = raw
    return result


def _metric(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = raw.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _receiving_yprr(datasets: dict[str, dict[str, Any]]) -> float | None:
    raw = datasets.get("receiving") or {}
    direct = _metric(raw, "yards_per_route_run", "yprr")
    if direct is not None:
        return direct
    yards = _metric(raw, "yards")
    routes = _metric(raw, "routes")
    return (yards / routes) if yards is not None and routes and routes > 0 else None


def _rushing_metrics(datasets: dict[str, dict[str, Any]]) -> dict[str, float | None]:
    raw = datasets.get("rushing") or {}
    bay = _metric(raw, "breakaway_yards", "breakaway_yardage", "breakaway_yards_total")
    elu = _metric(raw, "elusive_rating", "elu")
    ycoa = _metric(raw, "yards_after_contact_per_attempt", "yco_per_attempt", "yco_attempt")
    if ycoa is None:
        yco = _metric(raw, "yards_after_contact")
        attempts = _metric(raw, "attempts")
        if yco is not None and attempts and attempts > 0:
            ycoa = yco / attempts
    return {"BAY": bay, "ELU": elu, "YCOA": ycoa}


def _defense_metrics(datasets: dict[str, dict[str, Any]]) -> dict[str, float | None]:
    pass_rush = datasets.get("pass_rush") or {}
    run_defense = datasets.get("run_defense_detail") or datasets.get("run_defense") or {}
    coverage = datasets.get("coverage") or {}
    return {
        "PRWR": _metric(pass_rush, "pass_rush_win_rate", "win_rate"),
        "STOP": _metric(run_defense, "run_stop_percent", "stop_percent"),
        "QBR": _metric(coverage, "qb_rating_against", "passer_rating_against"),
    }


def _add_column_if_present(table: Table, key: str, label: str, fmt: str,
                           title: str) -> None:
    if any(row.get(key) is not None for row in table.rows):
        table.columns.append(Column(key=key, label=label, format=fmt, title=title))


def _enrich_pff(repository, groups: list[dict[str, Any]], pff_season: int) -> None:
    player_ids = [
        str(row.get("player_id"))
        for group in groups for row in group["table"].rows
        if row.get("player_id")
    ]
    metrics = _raw_metrics_by_player(repository, player_ids, pff_season)
    for group in groups:
        category = group.get("category")
        table = group["table"]
        for row in table.rows:
            datasets = metrics.get(str(row.get("player_id") or ""), {})
            if category == "receiving":
                row["YPRR"] = _receiving_yprr(datasets)
            elif category == "rushing":
                row.update(_rushing_metrics(datasets))
            elif category == "defense":
                row.update(_defense_metrics(datasets))
        if category == "receiving":
            _add_column_if_present(table, "YPRR", "YPRR", "f2",
                                   "PFF yards per route run")
        elif category == "rushing":
            _add_column_if_present(table, "BAY", "BAY", "int",
                                   "PFF breakaway yards")
            _add_column_if_present(table, "ELU", "ELU", "f1",
                                   "PFF elusive rating")
            _add_column_if_present(table, "YCOA", "YCO/A", "f2",
                                   "PFF yards after contact per attempt")
        elif category == "defense":
            _add_column_if_present(table, "PRWR", "PR Win%", "f1",
                                   "PFF pass-rush win rate")
            _add_column_if_present(table, "STOP", "Run Stop%", "f1",
                                   "PFF run-stop percentage")
            _add_column_if_present(table, "QBR", "QBR Allowed", "f1",
                                   "PFF passer rating allowed in coverage")


def _ordered_leader_groups(original, repository, leaders, season, *, include_team=True, limit=None):
    leaders = _full_team_leaders(repository, leaders, season, include_team)
    # Team-v-team production panels are filtered by football-specific usage
    # qualifiers in the repository. Once a player qualifies, do not truncate the
    # display to an arbitrary top-N; the scroll window is the presentation limit.
    render_limit = limit if include_team else None
    rendered = original(leaders, season, include_team=include_team, limit=render_limit)
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
    pff_season = int(leaders.get("season") or season - 1)
    _enrich_pff(repository, result, pff_season)
    return result


def install_production_display(app) -> None:
    if app.extensions.get("production_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    original_production = views.production_groups
    original_leaders = views.leader_groups

    def production_groups(production, season, **kwargs):
        # Pass keywords through rather than naming them: this wrapper only
        # reorders what the view returns and has no interest in what the
        # view was asked for.
        return _ordered_production_groups(
            original_production, production, season, **kwargs)

    def leader_groups(leaders, season, *, include_team=True, limit=None):
        return _ordered_leader_groups(
            original_leaders, repository, leaders, season,
            include_team=include_team, limit=limit)

    views.production_groups = production_groups
    views.leader_groups = leader_groups
    app.extensions["production_display_installed"] = True
