"""Hourly Render Cron trigger with Eastern-time schedule selection."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def trigger_if_due(
    *, now: datetime | None = None,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    zone_name = os.getenv("CFB_REFRESH_TIMEZONE", "America/New_York")
    hours = {int(value.strip()) for value in
             os.getenv("CFB_REFRESH_HOURS", "6,12,18,23").split(",") if value.strip()}
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone_name))
    if current.hour not in hours:
        return {"status": "skipped", "reason": "outside_refresh_hours",
                "local_time": current.isoformat()}

    url = (os.getenv("CFB_REFRESH_URL") or "").strip()
    token = (os.getenv("CFB_REFRESH_TOKEN") or "").strip()
    if not url or not token:
        raise RuntimeError("CFB_REFRESH_URL and CFB_REFRESH_TOKEN are required")
    request = Request(
        url, method="POST", data=b"",
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": "cfb-intelligence-render-cron/1.0"},
    )
    with opener(request, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
        status = getattr(response, "status", 200)
    if not 200 <= status < 300:
        raise RuntimeError(f"refresh endpoint returned HTTP {status}: {body[:200]}")
    return {"status": "triggered", "http_status": status,
            "local_time": current.isoformat(), "response": body[:500]}


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
