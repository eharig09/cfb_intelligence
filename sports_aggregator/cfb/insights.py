"""Early explainable attention scoring for Phase 1 dashboard ordering."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.rivalry_install import install_rivalry_annotations


# Install first so the Week 0 wrapper below composes over the rivalry-decorated
# upcoming-game method rather than replacing it. Both layers only mutate
# detached row dictionaries; SQLite remains canonical CFBD data.
install_rivalry_annotations()


def _rank_points(rank: int | None) -> float:
    return max(0.0, (26 - rank) * 1.35) if rank else 0.0


def _week_one_start(season: int) -> date:
    """First Thursday of the conventional Week 1 Labor Day window.

    CFBD can label the late-August opening slate as Week 1 even when the public
    schedule calls it Week 0. The dashboard should follow the football-week
    convention without rewriting the canonical schedule stored in SQLite.
    """
    september_first = date(int(season), 9, 1)
    labor_day = september_first + timedelta(days=(7 - september_first.weekday()) % 7)
    return labor_day - timedelta(days=4)


def _display_week(game: dict[str, Any]) -> int | None:
    raw = game.get("week")
    try:
        raw_week = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        raw_week = None

    season = game.get("season")
    start_value = game.get("start_date")
    if season is None or not start_value:
        return raw_week
    try:
        start = datetime.fromisoformat(str(start_value).replace("Z", "+00:00")).date()
        if start < _week_one_start(int(season)):
            return 0
    except (TypeError, ValueError):
        pass
    return raw_week


def _normalize_display_weeks(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize football week labels on detached repository row dictionaries."""
    for game in games:
        game["week"] = _display_week(game)
    return games


# The Today route determines its nearest week immediately after calling
# repository.upcoming_games(), before games_to_watch() gets a chance to
# normalize copies. Normalize the detached row dictionaries at that boundary
# so the week heading, weekly slice, and attention cards all use the same Week
# 0 convention. The database itself remains untouched.
_original_upcoming_games = CFBRepository.upcoming_games


def _upcoming_games_with_display_week(
    self: CFBRepository, season: int, limit: int = 16
) -> list[dict[str, Any]]:
    return _normalize_display_weeks(_original_upcoming_games(self, season, limit))


if not getattr(CFBRepository.upcoming_games, "_week_zero_normalized", False):
    _upcoming_games_with_display_week._week_zero_normalized = True
    CFBRepository.upcoming_games = _upcoming_games_with_display_week


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
    """Rank games from the nearest football week, including Week 0.

    The dashboard previously ranked the whole upcoming query at once. During
    Week 0 that let higher-profile Week 1 games crowd the opening slate out of
    the cards. Normalize display week on copies, select the nearest week, then
    apply the existing attention score inside that slate.
    """
    normalized: list[dict[str, Any]] = []
    for game in games:
        item = dict(game)
        item["week"] = _display_week(item)
        normalized.append(item)

    nearest = min(
        (item["week"] for item in normalized if item.get("week") is not None),
        default=None,
    )
    if nearest is not None:
        normalized = [item for item in normalized if item.get("week") == nearest]

    scored = []
    for item in normalized:
        item["attention_score"], item["attention_factors"] = score_game_attention(item)
        scored.append(item)
    scored.sort(key=lambda item: (-item["attention_score"], item["start_date"]))
    return scored[:limit]
