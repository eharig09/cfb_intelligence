"""Which refresh a moment calls for, decided where the schedule is stored.

The cron service that triggers a refresh runs in its own container with no
disk: the database mounts to the web service only, so the trigger cannot know
whether a game is being played. It fires on a clock and the web service, which
does have the schedule, chooses what to run.

Outside a game window that choice is the clock schedule it always was. Inside
one it is a game-day pass, which is the difference between a score appearing
when it happens and a score appearing at the next scheduled hour.
"""

from __future__ import annotations

import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

#: How long after kickoff a game is still worth polling for.
#:
#: A college football game runs about three and a half hours; weather delays
#: and overtime stretch that. The window closes on its own well before this,
#: because a game drops out of it the moment the sync marks it completed --
#: this is the ceiling for one that never does, so a single game whose result
#: never arrives cannot hold the refresh in game-day mode indefinitely.
GAME_WINDOW_HOURS = 6.0

#: How long before kickoff to start passing.
#:
#: Lines move most in the hours before a game, and the pass that catches the
#: first score is cheaper if it is already warm.
PREGAME_WINDOW_HOURS = 1.0


def _hours(name: str, default: str) -> set[int]:
    return {int(value.strip()) for value in os.getenv(name, default).split(",")
            if value.strip()}


def games_in_progress(repository, *, now: datetime | None = None,
                      season: int | None = None) -> int:
    """Games that have started, or are about to, and have no result stored yet.

    A game leaves this count when the sync marks it completed, so the count
    falls to zero on its own once every result has been read.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    opened = moment - timedelta(hours=GAME_WINDOW_HOURS)
    closes = moment + timedelta(hours=PREGAME_WINDOW_HOURS)
    repository.initialize()
    with closing(repository._connect()) as connection:
        if season is None:
            row = connection.execute(
                "SELECT MAX(season) FROM games").fetchone()
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
    """The refresh this moment calls for, and why.

    Returns the profile to run, or None to run nothing. The reason travels with
    it because this decision is made inside a cron trigger nobody is watching,
    and the trigger's log is the only place it can be read afterwards.
    """
    zone = ZoneInfo(timezone_name or os.getenv("CFB_REFRESH_TIMEZONE",
                                               "America/New_York"))
    moment = (now or datetime.now(timezone.utc)).astimezone(zone)

    live = games_in_progress(repository, now=moment, season=season)
    if live:
        return {"profile": "scores", "reason": "games_in_progress",
                "games": live, "local_time": moment.isoformat()}

    # No game to follow: the clock schedule, unchanged.
    heavy = _hours("CFB_REFRESH_HEAVY_HOURS", "6,23")
    light = _hours("CFB_REFRESH_HOURS", "6,12,18,23")
    if moment.hour in heavy:
        return {"profile": "heavy", "reason": "scheduled_hour", "games": 0,
                "local_time": moment.isoformat()}
    if moment.hour in light:
        return {"profile": "light", "reason": "scheduled_hour", "games": 0,
                "local_time": moment.isoformat()}
    return {"profile": None, "reason": "outside_refresh_hours", "games": 0,
            "local_time": moment.isoformat()}
