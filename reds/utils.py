"""
utils.py — shared config, helpers, TTL cache, HTTP fetching.
"""

import os
import re
import time
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

TEAM_ID_REDS = 113  # CIN in statsapi

# Season mode override: "auto" | "spring" | "regular"
SEASON_MODE = os.getenv("REDS_SEASON_MODE", "auto").strip().lower()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------

class TTLCache:
    """
    Thread-safe in-memory key/value cache with per-entry TTL.

    Usage:
        cache = TTLCache(default_ttl=300)          # 5-minute default
        cache.set("key", value, ttl=600)           # override per entry
        value = cache.get("key")                   # None if missing/expired
        value = cache.get_or_set("key", fn, ttl=300)
    """

    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, tuple[Any, float]] = {}   # key -> (value, expires_at)
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def get_or_set(self, key: str, fn: Callable, ttl: Optional[int] = None) -> Any:
        """Return cached value, or call fn(), cache it, and return it."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fn()
        if value is not None:
            self.set(key, value, ttl=ttl)
        return value

    def purge_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]


# Module-level shared cache instances (import these elsewhere)
# TTLs are in seconds.
news_cache    = TTLCache(default_ttl=300)    # 5 min  — news articles
stats_cache   = TTLCache(default_ttl=1800)   # 30 min — leaderboards / pybaseball
schedule_cache = TTLCache(default_ttl=600)   # 10 min — schedule
social_cache  = TTLCache(default_ttl=180)    # 3 min  — Bluesky / Reddit
statcast_cache = TTLCache(default_ttl=3600)  # 1 hr   — Statcast / charts


# ---------------------------------------------------------------------------
# Season helpers
# ---------------------------------------------------------------------------

def current_season_year() -> int:
    now = datetime.now()
    return now.year - 1 if now.month <= 2 else now.year


def get_season_mode(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    if SEASON_MODE in ("spring", "regular"):
        return SEASON_MODE
    return "spring" if now.month in (2, 3) else "regular"


def allowed_game_types(now: Optional[datetime] = None) -> set:
    return {"S"} if get_season_mode(now) == "spring" else {"R"}


# ---------------------------------------------------------------------------
# HTTP / BeautifulSoup helpers
# ---------------------------------------------------------------------------

def get_soup(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 15,
    retries: int = 3,
    backoff: float = 1.5,
) -> Optional[BeautifulSoup]:
    headers = headers or DEFAULT_HEADERS
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code in (403, 429):
                print(f"[get_soup] HTTP {resp.status_code} — {url}")
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            last_err = e
            time.sleep((backoff ** attempt) + random.random() * 0.25)
    print(f"[get_soup] FAILED {url} after {retries} tries: {last_err}")
    return None


def safe_pybaseball_call(func: Callable, *args, **kwargs) -> Optional[Any]:
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"WARNING: {func.__name__} failed: {e}")
        return None


def ensure_static_dir() -> None:
    os.makedirs("static", exist_ok=True)


# ---------------------------------------------------------------------------
# Date / text helpers
# ---------------------------------------------------------------------------

def parse_timestamp(timestamp_str: Optional[str]) -> Optional[datetime]:
    if not timestamp_str:
        return None
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        try:
            return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            print(f"Failed to parse timestamp: {timestamp_str}")
            return None


def extract_links(text: str) -> list:
    return re.findall(r"(https?://\S+)", text or "")


def sort_articles(articles: list) -> list:
    def _parse(date_str):
        if not date_str:
            return datetime.min.replace(tzinfo=timezone.utc)
        s = str(date_str).strip()
        try:
            dt = dateparser.parse(s)
            if dt is not None:
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except Exception:
            pass
        lower = s.lower()
        m = re.match(
            r"^\s*(\d+)\s*(d|day|days|h|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\s*$",
            lower,
        )
        if m:
            value, unit = int(m.group(1)), m.group(2)
            now = datetime.now(timezone.utc)
            if unit.startswith("d"):
                return now - timedelta(days=value)
            if unit.startswith("h"):
                return now - timedelta(hours=value)
            if unit.startswith("m"):
                return now - timedelta(minutes=value)
            return now - timedelta(seconds=value)
        return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(articles, key=lambda x: _parse(x.get("date", "")), reverse=True)


def humanize_time_ago(dt: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = int((now - dt).total_seconds())
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    if h < 48:
        return f"{h}h ago"
    return f"{h // 24}d ago"


def linkify_bsky_text(text: str) -> str:
    import html as _html
    safe = _html.escape(text or "")
    safe = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', safe)
    safe = re.sub(
        r"@([a-zA-Z0-9][a-zA-Z0-9.\-]+)",
        r'<a href="https://bsky.app/profile/\1" target="_blank" rel="noopener noreferrer">@\1</a>',
        safe,
    )
    safe = re.sub(
        r"#([A-Za-z0-9_]+)",
        r'<a href="https://bsky.app/hashtag/\1" target="_blank" rel="noopener noreferrer">#\1</a>',
        safe,
    )
    return safe


def chart_needs_refresh(path: str, max_age_hours: float = 6) -> bool:
    """Return True if the chart file is missing or older than max_age_hours."""
    if not os.path.exists(path):
        return True
    age = time.time() - os.path.getmtime(path)
    return age > max_age_hours * 3600
