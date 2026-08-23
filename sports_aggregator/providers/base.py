"""Provider contract implemented by RSS, API, scraper, and social adapters."""

from __future__ import annotations

from typing import Protocol

from sports_aggregator.models import Article


class ProviderFetchError(RuntimeError):
    """Raised when one source cannot return a usable response."""


class NewsProvider(Protocol):
    name: str

    def fetch(self) -> list[Article]:
        """Fetch and normalize articles from one source."""
        ...
