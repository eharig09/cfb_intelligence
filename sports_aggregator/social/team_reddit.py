"""Team subreddits, activated by the week's schedule rather than all at once.

Polling all 136 FBS team subreddits every cycle is technically easy and a bad
idea. The arithmetic: one request per subreddit, a rate limit around 60 requests
a minute, so a full sweep costs roughly two and a half minutes -- affordable. The
problem is what comes back. Team subreddits are mostly fan conversation, which
enters as COMMUNITY_REACTION and scores near the floor, so a full sweep adds
thousands of rows a day that no page will ever show.

What is genuinely valuable there is narrower: submissions linking to local beat
coverage that no national feed carries. So this module keeps every team
subreddit in the registry but activates a subset each cycle -- the teams playing
the week's highest-attention games, plus a small always-on tier -- and prefers
link submissions when it stores.

Nothing is discarded permanently. A team not activated this week simply is not
polled this week.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from sports_aggregator.cfb.repository import CFBRepository


TEAM_REDDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_subreddits (
 team_id INTEGER PRIMARY KEY, subreddit TEXT NOT NULL UNIQUE,
 tier TEXT NOT NULL DEFAULT 'SCHEDULED', verification_status TEXT NOT NULL DEFAULT 'unverified',
 subscribers INTEGER, platform_id TEXT, last_polled_at TEXT, last_error TEXT,
 updated_at TEXT NOT NULL
);
"""

#: Subreddits polled every cycle regardless of the schedule.
ALWAYS_ON_TIER = "ALWAYS"
#: Subreddits polled only when their team is in an activated game.
SCHEDULED_TIER = "SCHEDULED"

#: How many games' worth of teams to activate per cycle.
DEFAULT_ACTIVE_GAMES = 12
#: Teams inside this Elo rank are followed regardless of the week's schedule.
DEFAULT_TOP_ELO = 25
#: Elo movement, in points, that marks a team as worth watching this week.
ELO_MOVE_THRESHOLD = 40


def initialize(repository: CFBRepository) -> None:
    repository.initialize()
    with closing(repository._connect()) as connection:
        connection.executescript(TEAM_REDDIT_SCHEMA)


def register(repository: CFBRepository, entries: Iterable[dict[str, Any]]) -> int:
    """Record candidate team subreddits. Nothing is trusted until verified."""
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    rows = [(int(entry["team_id"]), str(entry["subreddit"]).removeprefix("r/"),
             entry.get("tier") or SCHEDULED_TIER, now)
            for entry in entries if entry.get("team_id") and entry.get("subreddit")]
    with closing(repository._connect()) as connection:
        connection.executemany(
            """INSERT INTO team_subreddits(team_id,subreddit,tier,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(team_id) DO UPDATE SET subreddit=excluded.subreddit,
               tier=excluded.tier,updated_at=excluded.updated_at""", rows)
        connection.commit()
    return len(rows)


def mark_verified(repository: CFBRepository, team_id: int, *, platform_id: str,
                  subscribers: int) -> None:
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    with closing(repository._connect()) as connection:
        connection.execute(
            """UPDATE team_subreddits SET verification_status='verified',
               platform_id=?,subscribers=?,last_error=NULL,updated_at=?
               WHERE team_id=?""", (platform_id, subscribers, now, team_id))
        connection.commit()


def mark_failed(repository: CFBRepository, team_id: int, error: str) -> None:
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    with closing(repository._connect()) as connection:
        connection.execute(
            """UPDATE team_subreddits SET verification_status='resolution_failed',
               last_error=?,updated_at=? WHERE team_id=?""", (error[:400], now, team_id))
        connection.commit()


def elo_movement(repository: CFBRepository, season: int) -> dict[int, dict[str, Any]]:
    """How far each team's Elo has moved across its rated games this season.

    A team climbing hard is the case the application exists to catch -- the one
    becoming interesting before it is obvious -- so movement, not just standing,
    decides who gets followed outside the always-on tier.
    """
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            """SELECT team_id,elo,start_date FROM (
                 SELECT home_team_id team_id,home_pregame_elo elo,start_date
                   FROM games WHERE season=? AND home_pregame_elo IS NOT NULL
                 UNION ALL
                 SELECT away_team_id team_id,away_pregame_elo elo,start_date
                   FROM games WHERE season=? AND away_pregame_elo IS NOT NULL
               ) ORDER BY start_date""", (season, season)).fetchall()
    history: dict[int, list[float]] = {}
    for row in rows:
        if row["team_id"] is not None and row["elo"] is not None:
            history.setdefault(row["team_id"], []).append(float(row["elo"]))
    movement = {}
    for team_id, values in history.items():
        movement[team_id] = {
            "first": values[0], "current": values[-1],
            "change": round(values[-1] - values[0], 1),
            "samples": len(values),
        }
    return movement


def active_subreddits(repository: CFBRepository, season: int, *,
                      games: int = DEFAULT_ACTIVE_GAMES,
                      top_elo: int = DEFAULT_TOP_ELO,
                      elo_move: float = ELO_MOVE_THRESHOLD) -> list[dict[str, Any]]:
    """The subreddits worth polling this cycle, each with the reason.

    Four ways in: the always-on tier, a team playing an activated game, a team
    inside the Elo top N, and a team whose Elo has moved sharply. The last one
    is deliberate: it reaches outside the power conferences without polling
    everyone.
    """
    initialize(repository)
    from sports_aggregator.cfb.insights import games_to_watch
    slate = games_to_watch(repository.upcoming_games(season, limit=120), limit=games)
    active_teams = {game["home_team_id"] for game in slate}
    active_teams |= {game["away_team_id"] for game in slate}
    elo = repository.team_elo(season)
    top_teams = {team_id for team_id, entry in elo.items()
                 if (entry.get("elo_rank") or 999) <= top_elo}
    movement = elo_movement(repository, season)
    movers = {team_id for team_id, entry in movement.items()
              if abs(entry["change"]) >= elo_move}
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT s.*,t.school,t.conference FROM team_subreddits s
               JOIN teams t USING(team_id)
               WHERE s.verification_status='verified'""")]
    selected = []
    for row in rows:
        team_id = row["team_id"]
        if row["tier"] == ALWAYS_ON_TIER:
            row["activation"] = "always-on tier"
        elif team_id in active_teams:
            row["activation"] = "team plays an activated game this week"
        elif team_id in top_teams:
            row["activation"] = f"inside the Elo top {top_elo}"
        elif team_id in movers:
            change = movement[team_id]["change"]
            row["activation"] = f"Elo moved {change:+.0f} this season"
        else:
            continue
        selected.append(row)
    return sorted(selected, key=lambda row: row["school"])


def poll_plan(repository: CFBRepository, season: int, *,
              games: int = DEFAULT_ACTIVE_GAMES,
              top_elo: int = DEFAULT_TOP_ELO) -> dict[str, Any]:
    """What a cycle would cost, so the trade-off is visible before it runs."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        registered = connection.execute(
            "SELECT COUNT(*) FROM team_subreddits").fetchone()[0]
        verified = connection.execute(
            "SELECT COUNT(*) FROM team_subreddits WHERE verification_status='verified'"
        ).fetchone()[0]
    active = active_subreddits(repository, season, games=games, top_elo=top_elo)
    return {
        "registered": registered,
        "verified": verified,
        "active_this_cycle": len(active),
        "requests_per_cycle": len(active),
        # Reddit's authenticated limit is about 60 requests a minute.
        "estimated_minutes": round(len(active) / 60, 1),
        "subreddits": [
            {"team": row["school"], "subreddit": f"r/{row['subreddit']}",
             "why": row["activation"]}
            for row in active
        ],
    }
