"""Rendered-page caching, keyed to the data and code the page was built from.

Pages here are almost entirely CPU work, so rendered HTML is cached aggressively.
The cache key carries both a database data version and a deployed-code version:
refresh writes invalidate data-backed pages, while a new Render deploy invalidates
HTML produced by older presentation code even when the persistent database has not
changed.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import current_app, request
from flask_caching import Cache


cache = Cache()
DEFAULT_PAGE_CACHE_SECONDS = 900
CACHE_KEY_VERSION = "page-v2"


def page_cache_seconds() -> int:
    raw = (os.getenv("CFB_PAGE_CACHE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_PAGE_CACHE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PAGE_CACHE_SECONDS


def data_version_seconds() -> int:
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
    configured = current_app.config.get("CFB_DATABASE_PATH") or ""
    if not configured:
        return "0"
    database = Path(configured)
    bucket_seconds = data_version_seconds()
    return "-".join(
        _version_stamp(candidate, bucket_seconds)
        for candidate in (database, database.with_name(database.name + "-wal"))
    )


def code_version() -> str:
    """Version rendered HTML by deployment as well as persistent data.

    Render exposes RENDER_GIT_COMMIT for Git-backed deploys.  Other environments
    can set CFB_PAGE_CACHE_CODE_VERSION explicitly; the static schema token still
    provides a manual fallback for local/non-Render deployments.
    """
    explicit = (os.getenv("CFB_PAGE_CACHE_CODE_VERSION") or "").strip()
    if explicit:
        return explicit
    commit = (os.getenv("RENDER_GIT_COMMIT") or "").strip()
    if commit:
        return commit[:16]
    return CACHE_KEY_VERSION


def caching_disabled() -> bool:
    return bool(current_app.config.get("TESTING")) or page_cache_seconds() <= 0


def page_key() -> str:
    """Full request identity plus data and deployed-code versions."""
    return f"page:{code_version()}:{data_version()}:{request.full_path}"


def cached_page(view):
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
        if isinstance(rendered, str):
            cache.set(key, rendered, timeout=page_cache_seconds())
        return rendered

    return wrapper
