"""League aggregation orchestration, deduplication, and short-lived caching."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Callable, Iterable, Mapping

from sports_aggregator.catalog import list_leagues
from sports_aggregator.models import Article, LeagueConfig
from sports_aggregator.providers import NewsProvider, RSSNewsProvider


@dataclass(frozen=True, slots=True)
class SourceError:
    source: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "message": self.message}


@dataclass(frozen=True, slots=True)
class AggregationResult:
    league: LeagueConfig
    articles: tuple[Article, ...]
    errors: tuple[SourceError, ...]
    fetched_at: datetime

    def to_dict(self) -> dict:
        return {
            "league": {
                "slug": self.league.slug,
                "name": self.league.name,
                "sport": self.league.sport,
                "abbreviation": self.league.abbreviation,
            },
            "articles": [article.to_dict() for article in self.articles],
            "errors": [error.to_dict() for error in self.errors],
            "fetched_at": self.fetched_at.isoformat(),
        }


def _sort_key(article: Article) -> tuple[float, str]:
    timestamp = article.published_at.timestamp() if article.published_at else float("-inf")
    return timestamp, article.title.casefold()


class AggregationService:
    """Run independent providers concurrently without coupling them to Flask."""

    def __init__(
        self,
        providers: Mapping[str, Iterable[NewsProvider]],
        cache_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = {slug: tuple(items) for slug, items in providers.items()}
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, AggregationResult]] = {}
        self._lock = threading.Lock()

    def aggregate(self, league: LeagueConfig, force_refresh: bool = False) -> AggregationResult:
        if not force_refresh:
            with self._lock:
                cached = self._cache.get(league.slug)
                if cached and cached[0] > self._clock():
                    return cached[1]

        providers = self._providers.get(league.slug, ())
        indexed_results: dict[int, list[Article]] = {}
        indexed_errors: dict[int, SourceError] = {}

        if providers:
            with ThreadPoolExecutor(max_workers=min(len(providers), 8)) as executor:
                futures = {
                    executor.submit(provider.fetch): (index, provider)
                    for index, provider in enumerate(providers)
                }
                for future in as_completed(futures):
                    index, provider = futures[future]
                    try:
                        indexed_results[index] = future.result() or []
                    except Exception as exc:
                        indexed_errors[index] = SourceError(provider.name, str(exc))

        unique: dict[str, Article] = {}
        for index in range(len(providers)):
            for article in indexed_results.get(index, []):
                unique.setdefault(article.identity, article)

        result = AggregationResult(
            league=league,
            articles=tuple(sorted(unique.values(), key=_sort_key, reverse=True)),
            errors=tuple(indexed_errors[index] for index in sorted(indexed_errors)),
            fetched_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._cache[league.slug] = (
                self._clock() + self._cache_ttl_seconds,
                result,
            )
        return result


def build_default_service() -> AggregationService:
    providers = {
        league.slug: tuple(RSSNewsProvider(feed) for feed in league.feeds)
        for league in list_leagues()
    }
    return AggregationService(providers)
