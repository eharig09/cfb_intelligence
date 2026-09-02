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
        except requests.HTTPError as exc:
            # A 4xx from resolveHandle is the platform telling us the handle is
            # not a handle -- `skhanjr.bsky.social` has answered 400 on every
            # run since it was seeded and has never once resolved. A 5xx is the
            # platform having a bad day, which is worth failing over.
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None)
            permanent = code is not None and 400 <= code < 500
            return IdentityResolution(handle, None, None, None, "resolution_failed",
                                      str(exc), permanent=permanent)
        except Exception as exc:
            return IdentityResolution(handle, None, None, None, "resolution_failed", str(exc))

    def author_feed(self, did: str, limit: int = 20) -> list[dict]:
        """Return a recovery window of authored posts, including replies/threads.

        The old implementation fetched a single page and explicitly requested
        ``posts_no_replies``. That could permanently miss useful reporter thread
        updates whenever more than the CLI limit was published between refreshes.
        Keep at least a 50-item recovery window and follow cursors when needed.
        """
        target = min(max(int(limit or 0), 50), 250)
        feed_items: list[dict] = []
        cursor: str | None = None

        while len(feed_items) < target:
            page_size = min(100, target - len(feed_items))
            params = {
                "actor": did,
                "limit": page_size,
                # Include replies so reporter threads and follow-up details are
                # available to the intelligence pipeline.
                "filter": "posts_with_replies",
            }
            if cursor:
                params["cursor"] = cursor

            response = self.session.get(
                f"{self.base_url}/xrpc/app.bsky.feed.getAuthorFeed",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("feed") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise ValueError("Bluesky author feed returned an invalid payload")
            if not page:
                break

            feed_items.extend(page)
            cursor = str(payload.get("cursor") or "") if isinstance(payload, dict) else ""
            if not cursor:
                break

        return feed_items[:target]
