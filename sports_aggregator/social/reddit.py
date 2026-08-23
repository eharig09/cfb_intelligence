"""Public identity/activity validation for curated subreddit endpoints."""

from __future__ import annotations

import os

import praw
import requests

from sports_aggregator.social.models import EndpointResolution


class RedditCommunityClient:
    def __init__(self, base_url="https://www.reddit.com", session=None, timeout=15, reddit=None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.reddit = reddit

    def _oauth_client(self):
        if self.reddit is not None:
            return self.reddit
        values = (os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET"),
                  os.getenv("REDDIT_USER_AGENT"))
        if not all(values):
            return None
        return praw.Reddit(client_id=values[0], client_secret=values[1], user_agent=values[2])

    def resolve(self, subreddit: str) -> EndpointResolution:
        name = subreddit.removeprefix("r/").strip()
        key = f"reddit:subreddit:{name.casefold()}"
        try:
            oauth = self._oauth_client()
            if oauth is not None:
                community = oauth.subreddit(name)
                display = str(community.display_name)
                valid = display.casefold() == name.casefold()
                if not valid:
                    return EndpointResolution(key, "identity_mismatch", description="Subreddit identity did not match")
                subscribers = float(community.subscribers or 0)
                return EndpointResolution(
                    key, "verified", platform_id=f"t5_{community.id}",
                    resolved_url=f"https://www.reddit.com/r/{display}/",
                    display_name=f"r/{display}", description=str(community.public_description or ""),
                    activity_score=min(5.0, 1.0 + subscribers / 500_000),
                )
            response = self.session.get(
                f"{self.base_url}/r/{name}/about.json",
                headers={"User-Agent": "sports-news-aggregator/1.0 source-validator"},
                timeout=self.timeout,
            )
            response.raise_for_status(); data = response.json().get("data") or {}
            display = str(data.get("display_name") or "")
            valid = display.casefold() == name.casefold() and data.get("subreddit_type") != "private"
            if not valid:
                return EndpointResolution(key, "identity_mismatch", description="Subreddit identity did not match")
            subscribers = float(data.get("subscribers") or 0)
            return EndpointResolution(
                key, "verified", platform_id=f"r/{display}",
                resolved_url=f"https://www.reddit.com/r/{display}/",
                display_name=f"r/{display}", description=str(data.get("public_description") or ""),
                activity_score=min(5.0, 1.0 + subscribers / 500_000),
            )
        except Exception as exc:
            return EndpointResolution(key, "resolution_failed", description=str(exc))


class RedditContentClient:
    """Read submissions from curated subreddits through the authenticated API.

    Reddit is a discovery surface, not a reporting authority. This client returns
    plain dictionaries; deciding who gets reporting credit happens in the content
    repository, which credits the linked publisher rather than the subreddit.
    """

    def __init__(self, reddit=None):
        self.reddit = reddit

    def _client(self):
        if self.reddit is not None:
            return self.reddit
        values = (os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET"),
                  os.getenv("REDDIT_USER_AGENT"))
        if not all(values):
            raise RuntimeError("REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT are required")
        return praw.Reddit(client_id=values[0], client_secret=values[1], user_agent=values[2])

    def submissions(self, subreddit: str, limit: int = 25, listing: str = "hot") -> list[dict]:
        name = subreddit.removeprefix("r/").strip()
        community = self._client().subreddit(name)
        listings = {"hot": community.hot, "new": community.new, "top": community.top}
        if listing not in listings:
            raise ValueError(f"Unsupported listing: {listing}")
        return [self._normalize(submission, name) for submission in listings[listing](limit=limit)]

    @staticmethod
    def _normalize(submission, subreddit: str) -> dict:
        author = getattr(submission, "author", None)
        return {
            "id": f"t3_{submission.id}",
            "subreddit": subreddit,
            "title": str(submission.title or ""),
            "selftext": str(getattr(submission, "selftext", "") or ""),
            "url": str(getattr(submission, "url", "") or ""),
            "permalink": f"https://www.reddit.com{submission.permalink}",
            "domain": str(getattr(submission, "domain", "") or ""),
            "is_self": bool(getattr(submission, "is_self", False)),
            "link_flair_text": str(getattr(submission, "link_flair_text", "") or ""),
            "score": int(getattr(submission, "score", 0) or 0),
            "num_comments": int(getattr(submission, "num_comments", 0) or 0),
            "upvote_ratio": float(getattr(submission, "upvote_ratio", 0) or 0),
            "created_utc": float(getattr(submission, "created_utc", 0) or 0),
            "author": str(author) if author else "[deleted]",
            "over_18": bool(getattr(submission, "over_18", False)),
            "stickied": bool(getattr(submission, "stickied", False)),
        }
