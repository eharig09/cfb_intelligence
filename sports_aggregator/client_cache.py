"""How long a browser may keep what the app just sent.

Two halves of one mechanism. Static URLs carry a stamp derived from the file's
own bytes, so a URL identifies a specific version of a file forever; that is
what makes it safe to tell a browser never to ask about it again.

Before this, the stylesheet went out as `no-cache` with a hand-written `?v=`
that someone had to remember to bump. Three of them carried different dates and
the regenerated chart images carried none at all, so the two failure modes were
live at once: four revalidation round trips on every page load, and no way to
push a change to an image a browser had already seen.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from flask import Flask, Response, g, request


#: A year, which is the longest value RFC 9111 suggests anyone bother sending.
IMMUTABLE_SECONDS = 31_536_000

#: Rendered pages are public and identical for every reader, but they report
#: scores. Long enough to absorb a double click or a step back through history,
#: short enough that nobody is reading yesterday's number off a warm cache.
DEFAULT_PAGE_SECONDS = 60
DEFAULT_STALE_SECONDS = 300

_stamps: dict[str, tuple[int, int, str]] = {}


def page_seconds() -> int:
    raw = (os.getenv("CFB_CLIENT_CACHE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_PAGE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PAGE_SECONDS


def file_stamp(path: Path) -> str | None:
    """A short digest of the file's contents, or None if it is not readable.

    Memoized against size and mtime so a page that references a dozen assets
    hashes each of them once per process rather than once per request. Content
    rather than mtime because a redeploy rewrites every mtime, and refetching
    assets that did not change is the thing this is meant to avoid.
    """
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _stamps.get(key)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        digest = hashlib.blake2b(path.read_bytes(), digest_size=6).hexdigest()
    except OSError:
        return None
    _stamps[key] = (stat.st_mtime_ns, stat.st_size, digest)
    return digest


def install_client_caching(app: Flask) -> None:
    static_root = Path(app.static_folder) if app.static_folder else None

    @app.url_defaults
    def stamp_static_url(endpoint: str, values: dict) -> None:
        """Give every static URL a version derived from the file it points at."""
        if endpoint != "static" or static_root is None:
            return
        filename = values.get("filename")
        if not filename or "v" in values:
            return
        stamp = file_stamp(static_root / filename)
        if stamp:
            values["v"] = stamp

    @app.after_request
    def cache_headers(response: Response) -> Response:
        if response.status_code >= 400:
            return response
        if request.endpoint == "static":
            # Only a URL that names a version can promise this. An unstamped one
            # means the file was missing when the page was built, and a year is
            # the wrong answer for a file that is about to appear.
            if request.args.get("v"):
                response.headers["Cache-Control"] = (
                    f"public, max-age={IMMUTABLE_SECONDS}, immutable")
            return response
        # Set by `cached_page`, which is the decorator that already decides a
        # page is public and identical for everyone.
        if getattr(g, "public_page", False) and page_seconds() > 0:
            response.headers["Cache-Control"] = (
                f"public, max-age={page_seconds()}, "
                f"stale-while-revalidate={DEFAULT_STALE_SECONDS}")
        return response
