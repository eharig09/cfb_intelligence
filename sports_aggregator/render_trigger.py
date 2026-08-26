"""Render Cron trigger. A clock, and nothing more.

This runs in its own container with no disk: the database mounts to the web
service alone, so nothing here can see whether a game is being played. It used
to pick the profile from the hour, which is why a score posted at 3:30pm first
appeared at 6pm. It now asks for "auto" and the web service -- which has the
schedule in front of it -- decides between a game-day pass, the light refresh,
the heavy one, and doing nothing.

Set CFB_REFRESH_PROFILE to pin a profile and skip that decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def trigger_if_due(
    *, now: datetime | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    zone_name = os.getenv("CFB_REFRESH_TIMEZONE", "America/New_York")
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone_name))
    # "auto" means the web service chooses; the hour gate lives there now,
    # alongside the schedule it has to consult to know about a game in play.
    profile = (os.getenv("CFB_REFRESH_PROFILE") or "auto").strip().casefold()
    url = (os.getenv("CFB_REFRESH_URL") or "").strip()
    token = (os.getenv("CFB_REFRESH_TOKEN") or "").strip()
    if not url or not token:
        raise RuntimeError("CFB_REFRESH_URL and CFB_REFRESH_TOKEN are required")

    separator = "&" if "?" in url else "?"
    trigger_url = f"{url}{separator}{urlencode({'profile': profile})}"
    request = Request(
        trigger_url,
        method="POST",
        data=b"",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "cfb-intelligence-render-cron/1.0",
        },
    )
    with opener(request, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
        status = getattr(response, "status", 200)
    if not 200 <= status < 300:
        raise RuntimeError(f"refresh endpoint returned HTTP {status}: {body[:200]}")
    # A 200 rather than a 202 is the service saying this moment called for
    # nothing. That is a normal outcome now that the cron fires every quarter
    # hour, and it should not read as a refresh that happened.
    return {
        "status": "triggered" if status == 202 else "skipped",
        "requested": profile,
        "profile": _resolved_profile(body) or profile,
        "http_status": status,
        "local_time": current.isoformat(),
        "response": body[:500],
    }


def _resolved_profile(body: str) -> str | None:
    """What the service said it ran, when it answered in JSON."""
    try:
        return (json.loads(body) or {}).get("profile")
    except (json.JSONDecodeError, AttributeError):
        return None


def main() -> int:
    try:
        report = trigger_if_due()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
