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


def upcoming_games_rows(schedule: list[dict[str, Any]], team_id: int, after_date: str,
                        limit: int = 3) -> list[dict[str, Any]]:
    """The next few games on this team's schedule, chronological, not yet played.

    `schedule` must already be sorted by start_date (as `team_schedule` returns
    it) and labeled with `date_label` (as `_label_games` adds it).
    """
    rows = []
    for game in schedule:
        start = game.get("start_date")
        if not start or start <= after_date or game.get("completed"):
            continue
        home = game.get("home_team_id") == team_id
        rows.append({
            "result": "NEXT",
            "opponent": game.get("away_team") if home else game.get("home_team"),
            "site": "Neutral" if game.get("neutral_site") else ("Home" if home else "Away"),
            "date_label": game.get("date_label"),
            "opponent_elo": game.get("away_pregame_elo") if home else game.get("home_pregame_elo"),
            "game_id": game.get("game_id"),
        })
        if len(rows) >= limit:
            break
    return rows


def recent_form_rows(games: list[dict[str, Any]], *,
                     upcoming: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Completed games leading into a matchup, with what's next after it.

    `upcoming` (see `upcoming_games_rows`) appends a few "NEXT" cards, so the
    recent form leads somewhere instead of just stopping -- the same context
    the old, separate season-journey timeline carried, without duplicating the
    schedule table with a second list of every game in the season.
    """
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
    if upcoming:
        rows.extend(upcoming)
    return rows


def _elo_win_prob(home_elo: float | None, away_elo: float | None) -> float | None:
    """Standard Elo win-probability curve, not an invented one."""
    if not home_elo or not away_elo:
        return None
    return 100 / (1 + 10 ** ((away_elo - home_elo) / 400))


def model_probability_track(game: dict[str, Any], fpi: dict[str, Any],
                            elo: dict[int, dict[str, Any]],
                            market: dict[str, Any]) -> dict[str, Any] | None:
    """Independent win-probability reads on the same axis, home team's share.

    Only sources with a real probability are plotted: FPI publishes one
    directly, Elo's comes from the standard logistic curve, and the market's
    comes from de-vigged moneylines (see `lines.game_lines`) -- never a spread
    converted through an uncalibrated guess.
    """
    home_id, away_id = game["home_team_id"], game["away_team_id"]
    home_fpi = (fpi.get("teams") or {}).get(home_id) or {}
    home_elo_value = (elo.get(home_id) or {}).get("elo")
    away_elo_value = (elo.get(away_id) or {}).get("elo")
    models = []
    fpi_prob = home_fpi.get("game_projection")
    if fpi_prob is not None:
        models.append({"key": "fpi", "label": "FPI", "home_prob": round(float(fpi_prob), 1)})
    elo_prob = _elo_win_prob(home_elo_value, away_elo_value)
    if elo_prob is not None:
        models.append({"key": "elo", "label": "Elo", "home_prob": round(elo_prob, 1)})
    market_prob = market.get("consensus_home_win_prob")
    if market_prob is not None:
        models.append({"key": "market", "label": "Market", "home_prob": market_prob})
    if not models:
        return None
    return {
        "home_team": game["home_team"], "away_team": game["away_team"],
        "models": models,
        "spread_open": market.get("consensus_spread_open"),
        "spread_current": market.get("consensus_spread"),
    }


def team_trend_chart_data(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Weekly offense/defense series, shaped for a Chart.js line chart.

    Success and explosive rate are stored as 0-1 fractions (matching
    `team_advanced_stats`) and converted to percentages here, once, so the
    template and the chart never have to agree on that separately.
    """
    if not rows:
        return None
    labels = [f"W{row['week']}" for row in rows]

    def series(key: str, *, pct: bool = False) -> list[float | None]:
        out = []
        for row in rows:
            value = row.get(key)
            if value is None:
                out.append(None)
            else:
                out.append(round(value * 100, 1) if pct else round(value, 3))
        return out

    return {
        "labels": labels,
        "metrics": [
            {"key": "epa", "label": "EPA / play",
             "offense": series("epa_per_play"), "defense": series("defense_epa_per_play")},
            {"key": "success", "label": "Success rate",
             "offense": series("success_rate", pct=True),
             "defense": series("defense_success_rate", pct=True)},
            {"key": "explosive", "label": "Explosive rate",
             "offense": series("explosive_rate", pct=True),
             "defense": series("defense_explosive_rate", pct=True)},
        ],
    }


def game_shape(away_team: str, home_team: str, away_pace: dict[str, Any] | None,
               home_pace: dict[str, Any] | None, away_drives: float | None,
               home_drives: float | None, away_advanced: dict[str, Any] | None,
               home_advanced: dict[str, Any] | None) -> dict[str, Any]:
    """How each side tends to play, side by side, plus an expected possession count.

    Each row is each team's own real tendency, not a blended prediction -- the
    same "read two independent views, don't average them" approach the models
    and market panel takes. Expected possessions is the average of both teams'
    own actual drives/game, which is the honest version of that number: no
    league-average plays-per-drive constant stands in for a team this hasn't
    been measured for.
    """
    def clamp_bar(value: float | None, scale: float = 1.0) -> float:
        return max(0.0, min(100.0, value * scale)) if value is not None else 0.0

    def rate(pace, key):
        return round(100 * pace[key], 1) if pace and pace.get(key) is not None else None

    away_plays = (away_pace or {}).get("plays_per_game")
    home_plays = (home_pace or {}).get("plays_per_game")
    away_pass_rate = rate(away_pace, "pass_rate")
    home_pass_rate = rate(home_pace, "pass_rate")
    away_explosive = (away_advanced or {}).get("offense_explosiveness")
    home_explosive = (home_advanced or {}).get("offense_explosiveness")
    rows = [
        {"label": "Pace", "unit": " plays", "away": away_plays, "home": home_plays,
         "fmt": "f1", "bar": True, "bar_away": clamp_bar(away_plays),
         "bar_home": clamp_bar(home_plays)},
        {"label": "Pass rate", "unit": "%", "away": away_pass_rate, "home": home_pass_rate,
         "fmt": "f1", "bar": True, "bar_away": clamp_bar(away_pass_rate),
         "bar_home": clamp_bar(home_pass_rate)},
        # CFBD's "explosiveness" is average EPA-scale value per explosive play,
        # not a rate -- typically 0.7-1.8, so it is shown raw and only scaled
        # (not treated as a percentage) for the comparison bar.
        {"label": "Explosiveness", "unit": "",
         "away": round(away_explosive, 2) if away_explosive is not None else None,
         "home": round(home_explosive, 2) if home_explosive is not None else None,
         "fmt": "f2", "bar": True, "bar_away": clamp_bar(away_explosive, 55),
         "bar_home": clamp_bar(home_explosive, 55)},
        {"label": "Points / opportunity", "unit": "",
         "away": (away_advanced or {}).get("offense_points_per_opportunity"),
         "home": (home_advanced or {}).get("offense_points_per_opportunity"),
         "fmt": "f2", "bar": False, "bar_away": 0.0, "bar_home": 0.0},
    ]
    drive_values = [value for value in (away_drives, home_drives) if value is not None]
    expected_possessions = round(sum(drive_values) / len(drive_values), 1) if drive_values else None
    return {
        "away_team": away_team, "home_team": home_team, "rows": rows,
        "away_drives": round(away_drives, 1) if away_drives is not None else None,
        "home_drives": round(home_drives, 1) if home_drives is not None else None,
        "expected_possessions": expected_possessions,
    }
