"""Rendered-page caching, keyed to the data the page was built from.

Pages here are almost entirely CPU work: rendering one matchup page spends
1,312 ms of CPU against 1,368 ms of wall clock, so there is no I/O wait for the
GIL to overlap. Threads therefore buy nothing — eight concurrent requests for
one page took 21.8 seconds and throughput *fell* from 1.34/s to 0.37/s under
contention. A single Render instance could serve two or three people at once.

Caching is the whole answer, because the data behind these pages changes only
when a refresh runs, every six hours. A cached page costs microseconds.

Invalidation without cross-process signalling
---------------------------------------------

The refresh runs as a separate process, so it cannot reach into the web
worker's in-memory cache to clear it. Rather than build a channel between them,
the cache key carries a *data version* taken from the database's modification
time — including the write-ahead log, because under WAL a commit lands in
`-wal` and may not touch the main file until a checkpoint.

When a refresh writes, the version changes, every key changes, and the old
entries simply become unreachable. No signal, no coordination, and no window
where a page is served from data that has already been replaced.

`timeout` remains a backstop for anything that changes the database without
moving those timestamps, not the primary mechanism.

One consequence worth knowing: a process's first request touches the database
for one-time setup, which moves its timestamp, so that request lands under a
key nothing else will reuse. The cache settles from the second request onward.
The cost is a single extra render per process start.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import current_app, request
from flask_caching import Cache


#: Shared instance. Lives here rather than in the application module so route
#: blueprints can decorate with it without importing the app factory.
cache = Cache()

#: Backstop lifetime for a cached page, in seconds. Overridden by
#: CFB_PAGE_CACHE_SECONDS; zero disables page caching entirely.
DEFAULT_PAGE_CACHE_SECONDS = 900


def page_cache_seconds() -> int:
    raw = (os.getenv("CFB_PAGE_CACHE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_PAGE_CACHE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PAGE_CACHE_SECONDS


def data_version() -> str:
    """A token that changes whenever the database is written.

    Two `stat()` calls, which cost microseconds against the second-plus it
    takes to render any of these pages.
    """
    configured = current_app.config.get("CFB_DATABASE_PATH") or ""
    if not configured:
        return "0"
    database = Path(configured)
    stamps = []
    for candidate in (database, database.with_name(database.name + "-wal")):
        try:
            stamps.append(str(candidate.stat().st_mtime_ns))
        except OSError:
            stamps.append("0")
    return "-".join(stamps)


def caching_disabled() -> bool:
    """Tests assert on rendered output, so a stale page would be a false pass."""
    return bool(current_app.config.get("TESTING")) or page_cache_seconds() <= 0


def page_key() -> str:
    """Full request identity plus the data version behind it.

    The query string matters: a team page carries `schedule_year`,
    `stats_year` and `stats_mode`, and each combination is a different page.
    """
    return f"page:{data_version()}:{request.full_path}"


def cached_page(view):
    """Cache a rendered page against the data it was built from.

    Deliberately a thin wrapper rather than `@cache.cached(...)` at each call
    site: the timeout is read per request so it can be changed by configuration
    without redeploying, and testing bypasses it entirely.
    """
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if caching_disabled():
            return view(*args, **kwargs)
        key = page_key()
        cached = cache.get(key)
        if cached is not None:
            return cached
        rendered = view(*args, **kwargs)
        # Only the rendered markup is stored. A Response object carries state
        # that does not survive being handed out twice, and an aborted request
        # raises rather than returning, so error pages never reach the cache.
        if isinstance(rendered, str):
            cache.set(key, rendered, timeout=page_cache_seconds())
        return rendered

    return wrapper
