"""Choose the smallest refresh profile a given moment needs."""

from __future__ import annotations

import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

GAME_WINDOW_HOURS = 6.0
PREGAME_WINDOW_HOURS = 1.0


def _hours(name: str, default: str) -> set[int]:
    return {int(value.strip()) for value in os.getenv(name, default).split(",")
            if value.strip()}


def games_in_progress(repository, *, now: datetime | None = None,
                      season: int | None = None) -> int:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    opened = moment - timedelta(hours=GAME_WINDOW_HOURS)
    closes = moment + timedelta(hours=PREGAME_WINDOW_HOURS)
    repository.initialize()
    with closing(repository._connect()) as connection:
        if season is None:
            row = connection.execute("SELECT MAX(season) FROM games").fetchone()
            season = row[0] if row else None
        if season is None:
            return 0
        return connection.execute(
            """SELECT COUNT(*) FROM games
               WHERE season=? AND completed=0
                 AND start_date>=? AND start_date<=?""",
            (season, opened.isoformat().replace("+00:00", "Z"),
             closes.isoformat().replace("+00:00", "Z"))).fetchone()[0]


def profile_for(repository, *, now: datetime | None = None,
                season: int | None = None,
                timezone_name: str | None = None) -> dict[str, Any]:
    """Return one bounded profile, or None when this cron tick needs no work.

    Live slates retain tiny quarter-hour score pulses. Outside games, scheduled
    work is split across several ``light`` ticks; tracked_refresh resolves each
    light tick to one small segment based on the local hour. Local reporting
    remains a separate sharded ``news`` profile. Overnight passes deliberately
    repeat those bounded jobs so the source cursor can move farther while
    traffic is low without increasing per-process memory pressure.
    """
    zone = ZoneInfo(timezone_name or os.getenv("CFB_REFRESH_TIMEZONE",
                                               "America/New_York"))
    moment = (now or datetime.now(timezone.utc)).astimezone(zone)
    live = games_in_progress(repository, now=moment, season=season)

    if live:
        profile = "results" if moment.minute == 0 else "scores"
        return {
            "profile": profile,
            "reason": "hourly_live_results" if profile == "results" else "games_in_progress",
            "games": live,
            "local_time": moment.isoformat(),
        }

    if moment.minute != 0:
        return {"profile": None, "reason": "between_scheduled_ticks", "games": 0,
                "local_time": moment.isoformat()}

    # Heavy is retained only for backward-compatible explicit/manual requests;
    # production scheduling leaves this set empty.
    heavy = _hours("CFB_REFRESH_HEAVY_HOURS", "")
    light = _hours("CFB_REFRESH_HOURS", "2,4,6,10,12,16,18,22,23")
    news = _hours("CFB_REFRESH_NEWS_HOURS", "1,3,5,8,14,20")
    if moment.hour in heavy:
        return {"profile": "heavy", "reason": "scheduled_hour", "games": 0,
                "local_time": moment.isoformat()}
    if moment.hour in light:
        return {"profile": "light", "reason": "scheduled_segment", "games": 0,
                "local_time": moment.isoformat()}
    if moment.hour in news:
        return {"profile": "news", "reason": "scheduled_news_shard", "games": 0,
                "local_time": moment.isoformat()}
    return {"profile": None, "reason": "outside_refresh_hours", "games": 0,
            "local_time": moment.isoformat()}
