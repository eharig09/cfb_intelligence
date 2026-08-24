"""Reusable RSS/Atom adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
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


def _publisher_id(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(domain or "").casefold()).strip("-")


def _preferred_local_feed(config: FeedConfig) -> str | None:
    """Prefer verified publisher RSS when a local-reporting config has one.

    Local reporting jobs historically passed only a Google News fallback URL even
    when the researched registry contained a native or sports RSS feed. Resolve
    the matching registry row from the endpoint key and transparently use the
    better feed when available.
    """
    if config.source_type != "local_reporting":
        return None
    if "news.google.com/rss" not in str(config.url):
        return None

    endpoint_key = str(config.source_endpoint_key or "")
    entity_key = str(config.source_entity_key or "")
    if not endpoint_key.startswith("rss:google-news:") or not entity_key.startswith("local-publisher:"):
        return None

    try:
        team_id = int(endpoint_key.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None
    publisher_id = entity_key.split("local-publisher:", 1)[-1]

    registry_path = Path("data/local_sources/cfb_local_source_registry.json")
    if not registry_path.exists():
        return None
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    for team in (registry.get("teams") or {}).values():
        try:
            current_team_id = int(team.get("team_id"))
        except (TypeError, ValueError):
            continue
        if current_team_id != team_id:
            continue
        for source in team.get("sources") or ():
            if _publisher_id(source.get("domain")) != publisher_id:
                continue
            for field in ("native_rss", "sports_rss"):
                value = str(source.get(field) or "").strip()
                if value.startswith(("http://", "https://")):
                    return value
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

    def _load_feed(self, url: str) -> Any:
        if self._parser is not None:
            return self._parser(url)
        response = requests.get(
            url,
            headers={"User-Agent": "sports-news-aggregator/1.0"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return feedparser.parse(response.content)

    def fetch(self) -> list[Article]:
        preferred = _preferred_local_feed(self.config)
        urls = [url for url in (preferred, self.config.url) if url]
        # Preserve order while avoiding a duplicate fallback.
        urls = list(dict.fromkeys(urls))

        feed = None
        used_url = self.config.url
        last_error: Exception | None = None
        for url in urls:
            try:
                candidate = self._load_feed(url)
                entries = _field(candidate, "entries", []) or []
                if _field(candidate, "bozo", False) and not entries:
                    detail = _field(candidate, "bozo_exception", "invalid feed")
                    raise ProviderFetchError(str(detail))
                feed = candidate
                used_url = url
                break
            except Exception as exc:
                last_error = exc
                continue

        if feed is None:
            raise ProviderFetchError(f"request failed: {last_error}") from last_error

        entries = _field(feed, "entries", []) or []
        articles: list[Article] = []
        discovery = "RSS_NATIVE" if preferred and used_url == preferred else "RSS"
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
                        discovered_via=discovery,
                        source_entity_key=self.config.source_entity_key,
                        source_endpoint_key=self.config.source_endpoint_key,
                    )
                )
            except ValueError:
                # A malformed entry should not discard the rest of a healthy feed.
                continue
        return articles
