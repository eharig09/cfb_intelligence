"""Reusable RSS/Atom adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from time import struct_time
from typing import Any, Callable

import feedparser
import requests

from sports_aggregator.models import Article, FeedConfig
from sports_aggregator.providers.base import ProviderFetchError


_HTML_TAG = re.compile(r"<[^>]+>")


def _field(entry: Any, name: str, default: Any = "") -> Any:
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _clean_text(value: Any) -> str:
    text = unescape(_HTML_TAG.sub(" ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _published_at(entry: Any) -> datetime | None:
    parsed: struct_time | None = _field(entry, "published_parsed", None)
    parsed = parsed or _field(entry, "updated_parsed", None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


class RSSNewsProvider:
    """Convert any feedparser-compatible source into normalized articles."""

    def __init__(
        self,
        config: FeedConfig,
        parser: Callable[[str], Any] | None = None,
        timeout_seconds: float = 12,
    ) -> None:
        self.config = config
        self.name = config.name
        self._parser = parser
        self._timeout_seconds = timeout_seconds

    def fetch(self) -> list[Article]:
        try:
            if self._parser is not None:
                # Parser injection keeps the adapter deterministic in unit tests.
                feed = self._parser(self.config.url)
            else:
                response = requests.get(
                    self.config.url,
                    headers={"User-Agent": "sports-news-aggregator/1.0"},
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                feed = feedparser.parse(response.content)
        except Exception as exc:
            raise ProviderFetchError(f"request failed: {exc}") from exc

        entries = _field(feed, "entries", []) or []
        if _field(feed, "bozo", False) and not entries:
            detail = _field(feed, "bozo_exception", "invalid feed")
            raise ProviderFetchError(str(detail))

        articles: list[Article] = []
        for entry in entries[: self.config.max_articles]:
            title = _clean_text(_field(entry, "title"))
            url = str(_field(entry, "link")).strip()
            if not title or not url:
                continue
            try:
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        source=self.config.name,
                        published_at=_published_at(entry),
                        author=_clean_text(_field(entry, "author")),
                        summary=_clean_text(_field(entry, "summary")),
                        source_type=self.config.source_type,
                        reliability=self.config.reliability,
                        publisher=self.config.name,
                        original_url=url,
                        discovered_via="RSS",
                        source_entity_key=self.config.source_entity_key,
                        source_endpoint_key=self.config.source_endpoint_key,
                    )
                )
            except ValueError:
                # A malformed entry should not discard the rest of a healthy feed.
                continue
        return articles
