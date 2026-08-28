"""Make model/market comparisons read with one consistent spread convention.

The upstream sources do not use the same sign semantics: CFBD stores the market
spread from the home team's perspective, while ESPN FPI publishes each team's
predicted scoring differential (positive means projected to outscore the
opponent). The game page should not expose those implementation details. For
line-like rows it uses the familiar betting convention instead: negative means
favorite, positive means underdog.
"""

from __future__ import annotations

from typing import Any


def _signed(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):+.1f}"
    except (TypeError, ValueError):
        return None


def _win_probability(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.0f}% win"
    except (TypeError, ValueError):
        return None


def normalize_model_comparison(table, game: dict[str, Any], fpi: dict[str, Any],
                               lines: dict[str, Any], elo: dict[int, dict[str, Any]] | None):
    """Normalize an existing model-comparison Table in place and return it."""
    teams = fpi.get("teams") or {}
    home_fpi = teams.get(game["home_team_id"]) or {}
    away_fpi = teams.get(game["away_team_id"]) or {}

    for row in table.rows:
        model = row.get("model")
        if model == "ESPN FPI":
            # FPI pred_point_diff is scoring margin. A team projected +7 in
            # scoring margin is displayed as -7 when expressed like a line.
            row["detail"] = "projected line"
            row["home_value"] = _signed(
                -float(home_fpi["pred_point_diff"])
                if home_fpi.get("pred_point_diff") is not None else None)
            row["away_value"] = _signed(
                -float(away_fpi["pred_point_diff"])
                if away_fpi.get("pred_point_diff") is not None else None)
            row["home_value_sub"] = _win_probability(home_fpi.get("game_projection"))
            row["away_value_sub"] = _win_probability(away_fpi.get("game_projection"))
            row["note"] = "FPI margin shown as a line"

        elif model == "Market":
            # CFBD's spread is already the home-team betting line. Preserve it
            # under the home column and invert it for the away team.
            spread = lines.get("consensus_spread")
            row["home_value"] = _signed(spread)
            row["away_value"] = _signed(-float(spread) if spread is not None else None)
            row["detail"] = f"consensus · {lines.get('count', 0)} book(s)"

        elif model == "CFBD Elo":
            home_elo = (elo or {}).get(game["home_team_id"]) or {}
            away_elo = (elo or {}).get(game["away_team_id"]) or {}
            if home_elo.get("elo") is not None and away_elo.get("elo") is not None:
                gap = int(home_elo["elo"]) - int(away_elo["elo"])
                leader = game["home_team"] if gap > 0 else game["away_team"] if gap < 0 else None
                row["note"] = (f"{leader} +{abs(gap)} Elo" if leader else "even Elo")

        elif model == "CFBD CORE":
            row["note"] = "model rating · not a point spread"

    table.note = "line rows: negative = favorite · ratings stay ratings"
    return table


def install_model_comparison_display() -> None:
    """Install the normalized presentation without changing stored source data."""
    from sports_aggregator.cfb import views

    current = views.model_comparison_table
    if getattr(current, "_normalized_line_display", False):
        return

    def wrapped(game, fpi, lines, elo, core=None):
        table = current(game, fpi, lines, elo, core)
        return normalize_model_comparison(table, game, fpi, lines, elo)

    wrapped._normalized_line_display = True
    views.model_comparison_table = wrapped
