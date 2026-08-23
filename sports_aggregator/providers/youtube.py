"""YouTube Data API adapter for already-curated channel endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any

import requests

from sports_aggregator.social.models import EndpointResolution


class YouTubeConfigurationError(RuntimeError):
    pass


#: Accepted key names, in order. YOUTUBE_API_KEY is canonical; YOUTUBE_API is
#: accepted because existing local environments already use that name.
API_KEY_VARIABLES = ("YOUTUBE_API_KEY", "YOUTUBE_API")


def configured_api_key() -> str:
    """First non-empty YouTube key found in the environment."""
    for name in API_KEY_VARIABLES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True, slots=True)
class VideoItem:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime
    duration: str
    thumbnail_url: str
    url: str


class YouTubeDataClient:
    """Uses stable channel/video IDs; it never scrapes channel pages."""

    def __init__(self, api_key: str | None = None,
                 base_url="https://www.googleapis.com/youtube/v3",
                 session=None, timeout=15) -> None:
        self.api_key = (api_key.strip() if api_key is not None else configured_api_key())
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        if not self.api_key:
            raise YouTubeConfigurationError(
                "Set YOUTUBE_API_KEY (or YOUTUBE_API) to use the YouTube Data API")
        response = self.session.get(
            f"{self.base_url}/{path}", params={**params, "key": self.api_key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("YouTube API returned a non-object payload")
        return payload

    def resolve_channel(self, *, endpoint_key: str, channel_id: str | None = None,
                        handle: str | None = None) -> EndpointResolution:
        if not channel_id and not handle:
            return EndpointResolution(endpoint_key, "resolution_failed",
                                      description="channel_id or handle is required")
        try:
            params: dict[str, Any] = {"part": "snippet,contentDetails", "maxResults": 1}
            if channel_id: params["id"] = channel_id
            else: params["forHandle"] = handle.removeprefix("@")
            items = self._get("channels", params).get("items") or []
            if len(items) != 1:
                return EndpointResolution(endpoint_key, "resolution_failed",
                                          description="YouTube channel was not uniquely resolved")
            channel = items[0]; resolved_id = str(channel.get("id") or "")
            snippet = channel.get("snippet") or {}
            if not resolved_id:
                return EndpointResolution(endpoint_key, "identity_mismatch",
                                          description="YouTube response omitted channel ID")
            return EndpointResolution(
                endpoint_key, "verified", platform_id=resolved_id,
                resolved_url=f"https://www.youtube.com/channel/{resolved_id}",
                display_name=str(snippet.get("title") or ""),
                description=str(snippet.get("description") or ""),
            )
        except Exception as exc:
            return EndpointResolution(endpoint_key, "resolution_failed", description=str(exc))

    def search_channels(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Find candidate channels by name.

        Search is a discovery step only. It returns plausible matches for review;
        it never establishes identity on its own, because a query for a show name
        readily returns clip channels and impersonators alongside the real one.
        """
        items = self._get("search", {
            "part": "snippet", "type": "channel", "q": query,
            "maxResults": min(max(max_results, 1), 25),
        }).get("items") or []
        channel_ids = [str((item.get("id") or {}).get("channelId") or "") for item in items]
        channel_ids = [item for item in channel_ids if item]
        if not channel_ids:
            return []
        return self.channels(channel_ids)

    def channels(self, channel_ids: list[str]) -> list[dict[str, Any]]:
        """Full channel records for stable IDs, including subscriber signals."""
        details = self._get("channels", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(channel_ids[:50]), "maxResults": 50,
        }).get("items") or []
        results = []
        for item in details:
            snippet = item.get("snippet") or {}
            statistics = item.get("statistics") or {}
            results.append({
                "channel_id": str(item.get("id") or ""),
                "title": str(snippet.get("title") or ""),
                "handle": str(snippet.get("customUrl") or ""),
                "description": str(snippet.get("description") or ""),
                "published_at": str(snippet.get("publishedAt") or ""),
                "subscribers": int(statistics.get("subscriberCount") or 0),
                "videos": int(statistics.get("videoCount") or 0),
                "views": int(statistics.get("viewCount") or 0),
                "uploads_playlist": (((item.get("contentDetails") or {})
                                      .get("relatedPlaylists") or {}).get("uploads") or ""),
            })
        return results

    def uploads(self, channel_id: str, max_results: int = 25) -> list[VideoItem]:
        channel_items = self._get("channels", {
            "part": "contentDetails", "id": channel_id, "maxResults": 1,
        }).get("items") or []
        if len(channel_items) != 1:
            return []
        playlist_id = (((channel_items[0].get("contentDetails") or {})
                       .get("relatedPlaylists") or {}).get("uploads"))
        if not playlist_id:
            return []
        items = self._get("playlistItems", {
            "part": "snippet,contentDetails", "playlistId": playlist_id,
            "maxResults": min(max(max_results, 1), 50),
        }).get("items") or []
        video_ids = [str((item.get("contentDetails") or {}).get("videoId") or "") for item in items]
        video_ids = [item for item in video_ids if item]
        details_by_id = {}
        if video_ids:
            detail_items = self._get("videos", {
                "part": "contentDetails", "id": ",".join(video_ids),
                "maxResults": min(len(video_ids), 50),
            }).get("items") or []
            details_by_id = {str(item.get("id")): item.get("contentDetails") or {}
                             for item in detail_items}
        videos: list[VideoItem] = []
        for item in items:
            snippet = item.get("snippet") or {}; details = item.get("contentDetails") or {}
            video_id = str(details.get("videoId") or "")
            published = str(details.get("videoPublishedAt") or snippet.get("publishedAt") or "")
            if not video_id or not published:
                continue
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc)
            thumbnails = snippet.get("thumbnails") or {}
            thumb = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {})
            videos.append(VideoItem(
                video_id=video_id, channel_id=channel_id,
                title=str(snippet.get("title") or ""),
                description=str(snippet.get("description") or ""), published_at=parsed,
                duration=str(details_by_id.get(video_id, {}).get("duration") or ""),
                thumbnail_url=str(thumb.get("url") or ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
            ))
        return videos
