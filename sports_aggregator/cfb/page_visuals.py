"""View models for compact, data-first visuals on CFB entity pages."""

from __future__ import annotations

import json
from typing import Any


def _pff_label(player: dict[str, Any], evidence: dict[str, Any]) -> str | None:
    """The position-relevant PFF grade, without presenting a blended score as one."""
    position = str(player.get("position") or "").upper()
    preferred = {
        "QB": ("passing",), "RB": ("rushing",), "FB": ("rushing",),
        "WR": ("receiving",), "TE": ("receiving",),
        "EDGE": ("pass_rush", "run_defense_detail"),
        "DE": ("pass_rush", "run_defense_detail"),
        "DL": ("run_defense_detail", "pass_rush"), "DT": ("run_defense_detail", "pass_rush"),
        "LB": ("run_defense_detail", "coverage"),
        "CB": ("coverage",), "DB": ("coverage",), "S": ("coverage",),
    }.get(position, ())
    datasets = evidence.get("pff_datasets") or {}
    for name in preferred:
        grade = (datasets.get(name) or {}).get("primary_grade")
        if grade is not None:
            return f"PFF {float(grade):.1f}"
    if position in {"OL", "OT", "OG", "G", "T", "C"}:
        packet = datasets.get("blocking") or {}
        try:
            metrics = json.loads(packet.get("metrics_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metrics = {}
        components = []
        for label, key in (("PB", "grades_pass_block"), ("RB", "grades_run_block")):
            try:
                if metrics.get(key) not in (None, ""):
                    components.append(f"{label} {float(metrics[key]):.1f}")
            except (TypeError, ValueError):
                pass
        if components:
            return " · ".join(components)
    return None


def _slot(player: dict[str, Any] | None, backup: dict[str, Any] | None,
          label: str, projection: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def entry(item):
        if not item:
            return None
        evidence = projection.get(str(item.get("player_id"))) or {}
        return {"name": item.get("name"), "player_id": item.get("player_id"),
                "grade": _pff_label(item, evidence),
                "status": "T" if item.get("arrival_type") == "TRANSFER_IN"
                else "R" if item.get("is_returner") else "N"}
    return {"label": label, "starter": entry(player), "backup": entry(backup)}


def depth_formations(depth_chart: dict[str, Any],
                     projection: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Eleven-person 11-offense and nickel 4-2-5 defense formations."""
    units = depth_chart.get("units") or {}
    offense = units.get("Offense") or {}
    defense = units.get("Defense") or {}

    def take(groups, name, count):
        players = list(groups.get(name) or [])
        starters = players[:count]
        backups = players[count:count * 2]
        starters += [None] * (count - len(starters))
        backups += [None] * (count - len(backups))
        return starters, backups

    qb, qb2 = take(offense, "Quarterback", 1)
    rb, rb2 = take(offense, "Backfield", 1)
    wr, wr2 = take(offense, "Wide receiver", 3)
    te, te2 = take(offense, "Tight end", 1)
    ol, ol2 = take(offense, "Offensive line", 5)
    interior, interior2 = take(defense, "Interior defensive line", 2)
    edge, edge2 = take(defense, "Edge", 2)
    lb, lb2 = take(defense, "Linebacker", 2)
    db, db2 = take(defense, "Defensive back", 5)
    make = lambda players, backups, labels: [
        _slot(player, backups[index], labels[index], projection)
        for index, player in enumerate(players)]
    # Rows read top to bottom as depth from the line of scrimmage, the same
    # line shared by both units, so a formation actually looks like one: the
    # line first (wide receivers split at its ends, tackle-to-tackle between
    # them), then anything lined up just off it, then the backfield or
    # secondary furthest back.
    return {
        "offense": {
            "line": make(wr[:1] + ol + te + wr[1:2], wr2[:1] + ol2 + te2 + wr2[1:2],
                        ["X", "LT", "LG", "C", "RG", "RT", "TE", "Z"]),
            "off_line": make(wr[2:], wr2[2:], ["SLOT"]),
            "backfield": [make(qb, qb2, ["QB"]), make(rb, rb2, ["RB"])],
            "count": 11, "label": "11 personnel",
        },
        "defense": {
            "line": make(edge[:1] + interior + edge[1:], edge2[:1] + interior2 + edge2[1:],
                        ["EDGE", "DT", "NT", "EDGE"]),
            "off_line": make(lb, lb2, ["MIKE", "WILL"]),
            "backfield": [make(db, db2, ["CB", "NB", "FS", "SS", "CB"])],
            "count": 11, "label": "4–2–5 nickel",
        },
    }


def recent_form_rows(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for game in games:
        points_for, points_against = game.get("points_for"), game.get("points_against")
        margin = (points_for - points_against
                  if points_for is not None and points_against is not None else None)
        opponent_elo = (game.get("away_pregame_elo") if game.get("site") == "Home"
                        else game.get("home_pregame_elo"))
        rows.append({**game, "full_score": game.get("score"), "margin": margin,
                     "margin_label": f"{margin:+d}" if margin is not None else None,
                     "shape": ("Blowout" if margin is not None and abs(margin) >= 21
                               else "One score" if margin is not None and abs(margin) <= 8
                               else "Multi-score" if margin is not None else None),
                     "opponent_elo": opponent_elo})
    return rows


def schedule_journey(schedule: list[dict[str, Any]], team_id: int,
                     elo: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for game in schedule:
        home = game.get("home_team_id") == team_id
        opponent_id = game.get("away_team_id") if home else game.get("home_team_id")
        team_points = game.get("home_points") if home else game.get("away_points")
        opponent_points = game.get("away_points") if home else game.get("home_points")
        complete = bool(game.get("completed"))
        result = None
        if complete and team_points is not None and opponent_points is not None:
            result = "W" if team_points > opponent_points else "L" if team_points < opponent_points else "T"
        rating = elo.get(opponent_id) or {}
        rows.append({
            "week": game.get("week"), "opponent": game.get("away_team") if home else game.get("home_team"),
            "site": "Home" if home else "Away", "result": result,
            "score": f"{team_points}–{opponent_points}" if complete and team_points is not None else game.get("date_label"),
            "opponent_elo": rating.get("elo"), "opponent_rank": rating.get("elo_rank"),
            "completed": complete, "game_id": game.get("game_id"),
        })
    return rows
