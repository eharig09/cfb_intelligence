"""Podcast RSS validation and episode normalization for curated feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import struct_time
from typing import Any

import feedparser
import requests

from sports_aggregator.social.models import EndpointResolution


@dataclass(frozen=True, slots=True)
class PodcastEpisode:
    episode_id: str
    title: str
    description: str
    published_at: datetime | None
    duration: str
    audio_url: str
    page_url: str


def _value(item: Any, name: str, default: Any = "") -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _date(entry: Any) -> datetime | None:
    parsed: struct_time | None = _value(entry, "published_parsed", None)
    parsed = parsed or _value(entry, "updated_parsed", None)
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None


class PodcastRSSClient:
    def __init__(self, session=None, timeout=15) -> None:
        self.session = session or requests.Session(); self.timeout = timeout

    def _parse(self, feed_url: str):
        response = self.session.get(
            feed_url, headers={"User-Agent": "sports-news-aggregator/1.0"},
            timeout=self.timeout,
        )
        response.raise_for_status(); return feedparser.parse(response.content)

    def resolve(self, feed_url: str, endpoint_key: str) -> EndpointResolution:
        try:
            parsed = self._parse(feed_url); feed = _value(parsed, "feed", {}) or {}
            title = str(_value(feed, "title", "") or "").strip()
            if not title:
                return EndpointResolution(endpoint_key, "identity_mismatch",
                                          description="Podcast feed has no title")
            return EndpointResolution(
                endpoint_key, "verified", platform_id=feed_url, resolved_url=feed_url,
                display_name=title, description=str(_value(feed, "subtitle", "") or ""),
            )
        except Exception as exc:
            return EndpointResolution(endpoint_key, "resolution_failed", description=str(exc))

    def episodes(self, feed_url: str, limit: int = 25) -> list[PodcastEpisode]:
        parsed = self._parse(feed_url); episodes = []
        for entry in (_value(parsed, "entries", []) or [])[:limit]:
            enclosures = _value(entry, "enclosures", []) or []
            audio_url = str(_value(enclosures[0], "href", "")) if enclosures else ""
            episode_id = str(_value(entry, "id", "") or audio_url or _value(entry, "link", ""))
            title = str(_value(entry, "title", "") or "").strip()
            if not episode_id or not title:
                continue
            episodes.append(PodcastEpisode(
                episode_id=episode_id, title=title,
                description=str(_value(entry, "summary", "") or ""),
                published_at=_date(entry),
                duration=str(_value(entry, "itunes_duration", "") or ""),
                audio_url=audio_url, page_url=str(_value(entry, "link", "") or ""),
            ))
        return episodes
