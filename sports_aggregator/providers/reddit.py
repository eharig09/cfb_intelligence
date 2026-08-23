"""Normalize Reddit as a discovery channel while crediting external publishers."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Callable, Iterable
from urllib.parse import urlsplit

import praw

from sports_aggregator.models import Article
from sports_aggregator.providers.base import ProviderFetchError


REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"}


def classify_submission(submission) -> str:
    title = str(getattr(submission, "title", "")).casefold()
    flair = str(getattr(submission, "link_flair_text", "") or "").casefold()
    text = f"{title} {flair}"
    if "postgame thread" in text: return "POSTGAME_THREAD"
    if "game thread" in text: return "GAME_THREAD"
    if "rumor" in text: return "RUMOR"
    if "analysis" in text: return "ANALYSIS"
    return "COMMUNITY_REACTION" if getattr(submission, "is_self", False) else "LINK_DISCOVERY"


def original_publisher(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host.removeprefix("www.")


class RedditCommunityProvider:
    """Returns external link discoveries; self-posts remain community evidence."""

    def __init__(self, subreddit: str, loader: Callable[[str, int], Iterable] | None = None,
                 limit: int = 30) -> None:
        self.subreddit = subreddit.removeprefix("r/")
        self.name = f"r/{self.subreddit}"
        self.limit = limit
        self._loader = loader

    def _submissions(self):
        if self._loader:
            return self._loader(self.subreddit, self.limit)
        credentials = (os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET"),
                       os.getenv("REDDIT_USER_AGENT"))
        if not all(credentials):
            raise ProviderFetchError("Reddit credentials are not configured")
        client = praw.Reddit(client_id=credentials[0], client_secret=credentials[1],
                             user_agent=credentials[2])
        return client.subreddit(self.subreddit).new(limit=self.limit)

    def fetch(self) -> list[Article]:
        articles: list[Article] = []
        try:
            submissions = self._submissions()
            for submission in submissions:
                url = str(getattr(submission, "url", "") or "").strip()
                host = (urlsplit(url).hostname or "").casefold()
                content_kind = classify_submission(submission)
                if not url or host in REDDIT_HOSTS or content_kind != "LINK_DISCOVERY":
                    continue
                publisher = original_publisher(url)
                if not publisher:
                    continue
                author = getattr(submission, "author", None)
                articles.append(Article(
                    title=str(getattr(submission, "title", "")).strip(), url=url,
                    original_url=url, source=publisher, publisher=publisher,
                    author="", published_at=datetime.fromtimestamp(
                        float(getattr(submission, "created_utc", 0)), tz=timezone.utc),
                    summary="", source_type="external_reporting",
                    reliability=2, discovered_via=self.name,
                    content_kind=content_kind,
                    discovery_endpoint_key=f"reddit:subreddit:{self.subreddit.casefold()}",
                ))
        except ProviderFetchError:
            raise
        except Exception as exc:
            raise ProviderFetchError(f"Reddit discovery failed: {exc}") from exc
        return articles
