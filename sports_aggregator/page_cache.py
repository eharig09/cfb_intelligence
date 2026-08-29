"""Rendered-page caching, keyed to the data the page was built from.

Pages here are almost entirely CPU work: rendering one matchup page spends
1,312 ms of CPU against 1,368 ms of wall clock, so there is no I/O wait for the
GIL to overlap. Threads therefore buy nothing — eight concurrent requests for
one page took 21.8 seconds and throughput *fell* from 1.34/s to 0.37/s under
contention. A single Render instance could serve two or three people at once.

Caching is the whole answer, because the data behind these pages changes only
when refresh jobs write new data. A cached page costs microseconds.

Invalidation without cross-process signalling
---------------------------------------------

The refresh runs as a separate process, so it cannot reach into the web
worker's in-memory cache to clear it. Rather than build a channel between them,
the cache key carries a *data version* taken from the database's modification
time — including the write-ahead log, because under WAL a commit lands in
`-wal` and may not touch the main file until a checkpoint.

On larger deployments every write can invalidate immediately. On constrained
instances, ``CFB_PAGE_CACHE_DATA_VERSION_SECONDS`` can deliberately coalesce
writes into a time bucket. This prevents a multi-step refresh from making an
expensive page render again after every individual SQLite commit. The normal
cache TTL remains a backstop, so the maximum staleness stays bounded.
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


def data_version_seconds() -> int:
    """Return the optional invalidation-coalescing window.

    Zero preserves the original behaviour: every database write gets a new
    version immediately. Production can use a larger window when refreshes are
    more frequent than users need the rendered HTML to change.
    """
    raw = (os.getenv("CFB_PAGE_CACHE_DATA_VERSION_SECONDS") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _version_stamp(path: Path, bucket_seconds: int) -> str:
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        return "0"
    if bucket_seconds <= 0:
        return str(modified_ns)
    bucket_ns = bucket_seconds * 1_000_000_000
    return str(modified_ns // bucket_ns)


def data_version() -> str:
    """A token that changes when the database data version advances."""
    configured = current_app.config.get("CFB_DATABASE_PATH") or ""
    if not configured:
        return "0"
    database = Path(configured)
    bucket_seconds = data_version_seconds()
    return "-".join(
        _version_stamp(candidate, bucket_seconds)
        for candidate in (database, database.with_name(database.name + "-wal"))
    )


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
    """Cache a rendered page against the data it was built from."""
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
