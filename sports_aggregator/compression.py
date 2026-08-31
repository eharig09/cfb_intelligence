"""Gzip text responses on the way out.

These pages are large and extremely compressible: the box score is 164 KB of
markup that becomes 14 KB, and the day page 241 KB that becomes 26 KB, because
a statistical table is the same forty tags repeated a thousand times. Nothing in
front of the app was doing this -- gunicorn does not compress, and there is no
CDN in the deployment -- so every reader was paying for the uncompressed body.

Written here rather than pulled in as a dependency because the whole of it is
one `after_request` hook, and the deployment builds on a constrained instance
where the requirements file is already the slowest part of a deploy.
"""

from __future__ import annotations

import gzip
import os

from flask import Flask, Response, request


#: Compression level. Six is zlib's default and the knee of the curve here:
#: level 1 gives up ~4 points of ratio to save half a millisecond, and level 9
#: spends twice the CPU of level 6 for a further 0.1%.
DEFAULT_LEVEL = 6

#: Only markup, styles, scripts and data. Images, video, fonts and PDFs are
#: already compressed, and running deflate over them costs CPU to add bytes.
COMPRESSIBLE_TYPES = frozenset({
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/rss+xml",
    "application/xml",
    "image/svg+xml",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
})

#: Below roughly a packet's worth there is nothing to win, and gzip's header can
#: make a very short body larger than it started.
MIN_BYTES = 1024

#: A static file is handed to the WSGI server as a file wrapper to stream rather
#: than a body in memory. The stylesheet is the largest single asset the site
#: serves (38 KB, on every cold visit) and compresses by 80%, so it is worth
#: reading one into memory to compress it -- but only one that is going to fit.
#: Anything larger stays streaming, uncompressed.
MAX_BUFFERED_BYTES = 1_000_000


def compression_level() -> int:
    raw = (os.getenv("CFB_GZIP_LEVEL") or "").strip()
    if not raw:
        return DEFAULT_LEVEL
    try:
        return min(9, max(0, int(raw)))
    except ValueError:
        return DEFAULT_LEVEL


def _is_compressible(response: Response) -> bool:
    """Whether this response is a candidate at all, ignoring the request."""
    if response.status_code < 200 or response.status_code in (204, 304):
        return False
    if response.headers.get("Content-Encoding"):
        return False
    if (response.mimetype or "").lower() not in COMPRESSIBLE_TYPES:
        return False
    if response.direct_passthrough:
        # Readable only if we already know it is small enough to hold.
        length = response.content_length
        return length is not None and length <= MAX_BUFFERED_BYTES
    return not response.is_streamed


def install_compression(app: Flask) -> None:
    """Register the hook. Call before any other `after_request` handler.

    Flask runs `after_request` functions in reverse registration order, so
    registering first means running last, which is what a transformation of the
    finished body wants.
    """
    level = compression_level()
    if level <= 0:
        return

    @app.after_request
    def compress(response: Response) -> Response:
        if not _is_compressible(response):
            return response

        # A shared cache must not hand a gzipped body to a client that did not
        # ask for one, so the header goes on every compressible response --
        # including the ones this request left uncompressed.
        response.headers.add("Vary", "Accept-Encoding")
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return response

        if (response.content_length or 0) < MIN_BYTES:
            return response
        # Reading the body of a file response is what turns it into a buffered
        # one; the flag has to come off first or Werkzeug keeps passing it through.
        response.direct_passthrough = False
        body = response.get_data()

        response.set_data(gzip.compress(body, level))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(response.content_length)
        # The entity the client receives is no longer byte-identical to the one
        # the strong validator described, so demote it to a weak one.
        etag, weak = response.get_etag()
        if etag and not weak:
            response.set_etag(etag, weak=True)
        return response
