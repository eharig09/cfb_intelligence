"""News provider adapters."""

from sports_aggregator.providers.base import NewsProvider, ProviderFetchError
from sports_aggregator.providers.rss import RSSNewsProvider

__all__ = ["NewsProvider", "ProviderFetchError", "RSSNewsProvider"]
