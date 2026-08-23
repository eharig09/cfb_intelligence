"""Early explainable attention scoring for Phase 1 dashboard ordering."""

from __future__ import annotations

from typing import Any


def _rank_points(rank: int | None) -> float:
    return max(0.0, (26 - rank) * 1.35) if rank else 0.0


def score_game_attention(game: dict[str, Any]) -> tuple[int, list[str]]:
    """Provisional transparent score; the richer importance model comes in Phase 9."""
    score = 0.0
    factors: list[str] = []
    home_rank = game.get("home_rank")
    away_rank = game.get("away_rank")
    if home_rank:
        score += _rank_points(home_rank)
        factors.append(f"{game['home_team']} ranked #{home_rank}")
    if away_rank:
        score += _rank_points(away_rank)
        factors.append(f"{game['away_team']} ranked #{away_rank}")
    if home_rank and away_rank:
        score += 10
        factors.append("ranked matchup")
    if game.get("conference_game"):
        score += 6
        factors.append("conference game")
    if game.get("neutral_site"):
        score += 2
        factors.append("neutral site")

    home_elo = game.get("home_pregame_elo")
    away_elo = game.get("away_pregame_elo")
    if home_elo and away_elo:
        quality = max(0, ((home_elo + away_elo) / 2 - 1300) / 35)
        closeness = max(0, 8 - abs(home_elo - away_elo) / 35)
        score += min(12, quality) + closeness
        if abs(home_elo - away_elo) <= 100:
            factors.append("competitive Elo matchup")
    return min(100, round(score)), factors


def games_to_watch(games: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    scored = []
    for game in games:
        item = dict(game)
        item["attention_score"], item["attention_factors"] = score_game_attention(item)
        scored.append(item)
    scored.sort(key=lambda item: (-item["attention_score"], item["start_date"]))
    return scored[:limit]
