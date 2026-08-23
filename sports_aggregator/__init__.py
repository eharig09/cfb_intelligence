"""League-agnostic news aggregation primitives."""

from sports_aggregator.catalog import get_league, list_leagues
from sports_aggregator.models import Article, FeedConfig, LeagueConfig
from sports_aggregator.service import AggregationResult, AggregationService

__all__ = [
    "AggregationResult",
    "AggregationService",
    "Article",
    "FeedConfig",
    "LeagueConfig",
    "get_league",
    "list_leagues",
]
