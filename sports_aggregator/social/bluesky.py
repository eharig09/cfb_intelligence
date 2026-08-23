"""Unauthenticated AT Protocol identity/profile resolution for curated sources."""

from __future__ import annotations

import requests

from sports_aggregator.social.models import IdentityResolution


class BlueskyIdentityClient:
    def __init__(self, base_url="https://public.api.bsky.app", session=None, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def resolve(self, handle: str) -> IdentityResolution:
        try:
            resolution = self.session.get(
                f"{self.base_url}/xrpc/com.atproto.identity.resolveHandle",
                params={"handle": handle}, timeout=self.timeout,
            )
            resolution.raise_for_status()
            did = resolution.json()["did"]
            profile_response = self.session.get(
                f"{self.base_url}/xrpc/app.bsky.actor.getProfile",
                params={"actor": did}, timeout=self.timeout,
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            current_handle = profile.get("handle")
            profile_did = profile.get("did")
            valid = profile_did == did and current_handle and current_handle.casefold() == handle.casefold()
            return IdentityResolution(
                requested_handle=handle, did=did if valid else None,
                current_handle=current_handle, display_name=profile.get("displayName"),
                status="verified" if valid else "identity_mismatch",
                description=profile.get("description") or "",
            )
        except Exception as exc:
            return IdentityResolution(handle, None, None, None, "resolution_failed", str(exc))

    def author_feed(self, did: str, limit: int = 20) -> list[dict]:
        response = self.session.get(
            f"{self.base_url}/xrpc/app.bsky.feed.getAuthorFeed",
            params={"actor": did, "limit": min(max(limit, 1), 100),
                    "filter": "posts_no_replies"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json(); feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            raise ValueError("Bluesky author feed returned an invalid payload")
        return feed
