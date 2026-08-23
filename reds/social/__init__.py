"""
social/__init__.py — Bluesky and Reddit fetching.

BUG FIX: the original fetch_limited_replies() referenced author_handle,
timestamp, text, and media from the outer loop scope — those variables
don't exist inside the nested function. Fixed by passing them explicitly
and removing the broken self-references.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import praw
import requests

from reds.utils import (
    parse_timestamp,
    extract_links,
    humanize_time_ago,
    linkify_bsky_text,
    social_cache,
)

BLUESKY_USERNAME = os.getenv("BLUESKY_USERNAME")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------

def bluesky_login() -> Optional[str]:
    if not BLUESKY_USERNAME or not BLUESKY_PASSWORD:
        print("[Bluesky] Credentials not set; skipping.")
        return None
    try:
        resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": BLUESKY_USERNAME, "password": BLUESKY_PASSWORD},
            timeout=20,
        )
        resp.raise_for_status()
        print("[Bluesky] Authenticated.")
        return resp.json().get("accessJwt")
    except Exception as e:
        print(f"[Bluesky] Auth failed: {e}")
        return None


def skeet_uri_to_url(handle: str, uri: str) -> str:
    if not handle or not uri:
        return ""
    try:
        rkey = uri.rstrip("/").split("/")[-1]
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    except Exception:
        return ""


def extract_bsky_media(post: dict) -> list:
    out = []
    embed = post.get("embed") or {}
    etype = embed.get("$type", "")

    if "app.bsky.embed.images" in etype:
        for img in embed.get("images", []) or []:
            out.append({
                "type": "image",
                "thumb": img.get("thumb", ""),
                "full": img.get("fullsize", ""),
                "alt": img.get("alt", ""),
            })

    if "app.bsky.embed.external" in etype:
        ext = embed.get("external") or {}
        out.append({
            "type": "external",
            "uri": ext.get("uri", ""),
            "title": ext.get("title", ""),
            "description": ext.get("description", ""),
            "thumb": ext.get("thumb", ""),
        })

    return out


def _fetch_replies(token: str, skeet_uri: str, cutoff: datetime, max_replies: int = 3) -> list:
    """
    FIX: no longer references outer-scope variables.
    All data is derived from the reply post object directly.
    """
    replies = []
    try:
        resp = requests.get(
            f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?post={skeet_uri}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if resp.status_code != 200:
            return replies

        for reply in (resp.json().get("thread", {}).get("replies", []) or [])[:max_replies]:
            post = reply.get("post", {})
            record = post.get("record", {})

            reply_timestamp = parse_timestamp(record.get("createdAt", ""))
            if not reply_timestamp:
                continue
            if reply_timestamp.tzinfo is None:
                reply_timestamp = reply_timestamp.replace(tzinfo=timezone.utc)
            if reply_timestamp < cutoff:
                continue

            reply_handle = post.get("author", {}).get("handle", "Unknown")
            reply_uri = post.get("uri", "")
            reply_text = record.get("text", "")

            replies.append({
                "author": reply_handle,
                "display_name": post.get("author", {}).get("displayName", "Unknown"),
                "avatar_url": post.get("author", {}).get("avatar", ""),
                "text": reply_text,
                "timestamp": reply_timestamp.isoformat(),
                "uri": reply_uri,
                "url": skeet_uri_to_url(reply_handle, reply_uri),
                "time_ago": humanize_time_ago(reply_timestamp),
                "html_text": linkify_bsky_text(reply_text),
                "media": extract_bsky_media(post),
                "links": extract_links(reply_text),
                "replies": [],
            })
    except Exception as e:
        print(f"[Bluesky] Reply fetch error for {skeet_uri}: {e}")
    return replies


_TRACKED_ACCOUNTS = [
    "ctrent.bsky.social",
    "msheldon.bsky.social",
    "nicholaspkirby.bsky.social",
    "dougdirt24.bsky.social",
    "gdubmlb.bsky.social",
    "redreporter.bsky.social",
    "mollyknight.bsky.social",
    "joeposnanski.bsky.social",
    "jmorris14.bsky.social",
    "enosarris.bsky.social",
    "keithlaw.bsky.social",
    "jaysonst.bsky.social",
    "ken-rosenthal.bsky.social",
    "peteabeglobe.bsky.social",
    "feinsand.bsky.social",
    "dgoold.bsky.social",
]


def fetch_reds_posts(token: Optional[str]) -> list:
    """Renamed from fetch_reds_skeets — 'posts' is the correct Bluesky term."""
    if not token:
        return []

    cached = social_cache.get("bluesky_posts")
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    posts = []

    for username in _TRACKED_ACCOUNTS:
        try:
            resp = requests.get(
                f"https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit=5",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if resp.status_code != 200:
                continue

            for item in (resp.json().get("feed", []) or [])[:5]:
                post = item.get("post", {})
                record = post.get("record", {})
                text = record.get("text", "")

                timestamp = parse_timestamp(record.get("createdAt", ""))
                if not timestamp:
                    continue
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp < cutoff:
                    continue

                skeet_uri = post.get("uri", "")
                author_handle = post.get("author", {}).get("handle", "Unknown")

                posts.append({
                    "author": author_handle,
                    "display_name": post.get("author", {}).get("displayName", "Unknown"),
                    "avatar_url": post.get("author", {}).get("avatar", ""),
                    "text": text,
                    "timestamp": timestamp.isoformat(),
                    "uri": skeet_uri,
                    "url": skeet_uri_to_url(author_handle, skeet_uri),
                    "time_ago": humanize_time_ago(timestamp),
                    "html_text": linkify_bsky_text(text),
                    "media": extract_bsky_media(post),
                    "links": extract_links(text),
                    "replies": _fetch_replies(token, skeet_uri, cutoff),
                })

        except Exception as e:
            print(f"[Bluesky] Feed fetch error for {username}: {e}")

    posts.sort(
        key=lambda x: parse_timestamp(x["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    print(f"[Bluesky] {len(posts)} posts")
    social_cache.set("bluesky_posts", posts)
    return posts


# Keep old name as an alias so existing templates don't break
fetch_reds_skeets = fetch_reds_posts


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def fetch_reddit_posts(subreddit: str, limit: int = 15) -> list:
    cache_key = f"reddit_{subreddit}"
    cached = social_cache.get(cache_key)
    if cached is not None:
        return cached

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    if not all([client_id, client_secret, user_agent]):
        print(f"[Reddit] Credentials not set; skipping r/{subreddit}.")
        return []

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        posts = []
        for sub in reddit.subreddit(subreddit).new(limit=limit):
            posts.append({
                "title": sub.title,
                "author": sub.author.name if sub.author else "Unknown",
                "score": sub.score,
                "comments": sub.num_comments,
                "link": f"https://www.reddit.com{sub.permalink}",
                "timestamp": datetime.utcfromtimestamp(sub.created_utc).strftime("%Y-%m-%d %H:%M:%S"),
                "subreddit": subreddit,
            })

        posts.sort(
            key=lambda x: datetime.strptime(x["timestamp"], "%Y-%m-%d %H:%M:%S"),
            reverse=True,
        )
        print(f"[Reddit] r/{subreddit}: {len(posts)} posts")
        social_cache.set(cache_key, posts)
        return posts

    except Exception as e:
        print(f"[Reddit] Error fetching r/{subreddit}: {e}")
        return []
