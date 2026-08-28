"""Conference-page enrichment and exact-match content moderation helpers.

This module keeps presentation-specific conference calculations out of the core
CFBD repository while reusing the repository's current Elo view and persisted
roster/movement data. It also owns the small exact-URL blocklist used by the
private data-status tools.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from typing import Any

from sports_aggregator.cfb.models import normalize_person_name


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def conference_schedule_elo(repository, conference: str, season: int) -> dict[str, Any]:
    """Current-opponent Elo splits for a conference schedule.

    Home/away figures include only remaining games. Conference/non-conference
    figures include the full stored schedule. A conference-vs-conference game is
    counted once from each member team's perspective because it occupies one
    schedule slot for each member.
    """
    current_elo = repository.team_elo(season)
    games = repository.conference_games(conference, season, limit=500)
    remaining_home: list[float] = []
    remaining_away: list[float] = []
    conference_opponents: list[float] = []
    nonconference_opponents: list[float] = []

    for game in games:
        for side in ("home", "away"):
            if game.get(f"{side}_conference") != conference:
                continue
            other = "away" if side == "home" else "home"
            opponent_id = game.get(f"{other}_team_id")
            opponent = current_elo.get(opponent_id) or {}
            rating = opponent.get("elo")
            if rating is None:
                continue
            value = float(rating)

            opponent_is_conference = game.get(f"{other}_conference") == conference
            (conference_opponents if opponent_is_conference else nonconference_opponents).append(value)

            if not game.get("completed") and not game.get("neutral_site"):
                if side == "home":
                    remaining_home.append(value)
                else:
                    remaining_away.append(value)

    return {
        "remaining_home": _average(remaining_home),
        "remaining_home_games": len(remaining_home),
        "remaining_away": _average(remaining_away),
        "remaining_away_games": len(remaining_away),
        "conference": _average(conference_opponents),
        "conference_games": len(conference_opponents),
        "nonconference": _average(nonconference_opponents),
        "nonconference_games": len(nonconference_opponents),
    }


def _team_conferences(connection: sqlite3.Connection) -> dict[str, str | None]:
    return {row["school"]: row["conference"] for row in connection.execute(
        "SELECT school,conference FROM teams"
    )}


def _movement_maps(connection: sqlite3.Connection, season: int):
    transfers: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(
        "SELECT normalized_name,origin,destination FROM player_transfers WHERE season=?",
        (season,),
    ):
        transfers.setdefault(row["normalized_name"], []).append(dict(row))
    drafted_ids: set[str] = set()
    drafted_names: set[str] = set()
    for row in connection.execute(
        "SELECT college_athlete_id,normalized_name FROM draft_picks WHERE draft_year=?",
        (season,),
    ):
        if row["college_athlete_id"]:
            drafted_ids.add(str(row["college_athlete_id"]))
        if row["normalized_name"]:
            drafted_names.add(str(row["normalized_name"]))
    return transfers, drafted_ids, drafted_names


def decorate_conference_leaders(repository, groups, leaders: dict[str, Any],
                                 conference: str, season: int):
    """Accent conference leader rows by current roster disposition.

    Only the player cell is accented, matching the understated treatment used
    by the team-page production tables. The source leaderboard and ordering are
    unchanged.
    """
    if not groups:
        return groups
    source_season = int(leaders.get("season") or season)
    repository.initialize()
    with closing(repository._connect()) as connection:
        current_by_id: dict[str, dict[str, Any]] = {}
        current_by_name: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            "SELECT player_id,normalized_name,team FROM players WHERE season=?",
            (season,),
        ):
            item = dict(row)
            current_by_id[str(row["player_id"])] = item
            current_by_name.setdefault(str(row["normalized_name"]), []).append(item)
        team_conference = _team_conferences(connection)
        transfers, drafted_ids, drafted_names = _movement_maps(connection, season)

    def transfer_status(name_key: str, source_team: str | None, current_team: str | None):
        candidates = transfers.get(name_key) or []
        movement = next((item for item in candidates
                         if current_team and item.get("destination") == current_team), None)
        if movement is None and source_season < season:
            movement = next((item for item in candidates
                             if source_team and item.get("origin") == source_team), None)
        if movement is None:
            return None
        origin = movement.get("origin")
        destination = movement.get("destination") or current_team
        origin_conf = team_conference.get(origin)
        destination_conf = team_conference.get(destination)
        in_conference = origin_conf == conference and destination_conf == conference
        return {
            "class": "state-transfer-in-conference" if in_conference else "state-transferred",
            "label": (f"In-conference transfer → {destination}" if in_conference
                      else f"Transferred → {destination or 'TBD'}"),
        }

    for group in groups:
        table = group.get("table") if isinstance(group, dict) else None
        if table is None:
            continue
        for row in table.rows:
            player_id = str(row.get("player_id") or "")
            player_name = str(row.get("player") or "")
            name_key = normalize_person_name(player_name)
            source_team = row.get("team")
            current = current_by_id.get(player_id)
            if current is None:
                matches = current_by_name.get(name_key) or []
                current = matches[0] if len(matches) == 1 else None
            current_team = current.get("team") if current else None

            movement = transfer_status(name_key, source_team, current_team)
            if movement and (source_season < season or current_team == source_team):
                row["player_class"] = movement["class"]
                row["player_sub"] = movement["label"]
                continue

            if current_team:
                if source_team == current_team:
                    row["player_class"] = "state-conference-returning"
                    row["player_sub"] = "Returning"
                else:
                    destination_conf = team_conference.get(current_team)
                    in_conference = destination_conf == conference
                    row["player_class"] = (
                        "state-transfer-in-conference" if in_conference else "state-transferred"
                    )
                    row["player_sub"] = (
                        f"In-conference transfer → {current_team}" if in_conference
                        else f"Transferred → {current_team}"
                    )
                continue

            if movement:
                row["player_class"] = movement["class"]
                row["player_sub"] = movement["label"]
            elif player_id in drafted_ids or name_key in drafted_names:
                row["player_class"] = "state-departed"
                row["player_sub"] = "Drafted"
            else:
                row["player_class"] = "state-departed"
                row["player_sub"] = "Graduated / departed"
    return groups


def _relative_age(value: str | None, *, now: datetime | None = None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(timezone.utc)
    seconds = max(0.0, (current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    hours = seconds / 3600
    if hours < 1:
        return "<1h"
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours // 24)
    if days < 7:
        return f"{days}d"
    return f"{max(1, days // 7)}w"


def market_freshness(lines: dict[str, Any]) -> str | None:
    """Compact age of the newest provider quote in a game market packet."""
    stamps = [row.get("fetched_at") for row in lines.get("providers") or [] if row.get("fetched_at")]
    if not stamps:
        return None
    try:
        newest = max(stamps, key=lambda value: datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        newest = max(stamps)
    return _relative_age(newest)


def install_market_freshness_note() -> None:
    """Attach compact quote age to the existing Market table note once."""
    from sports_aggregator.cfb import views

    if getattr(views.market_table, "_freshness_wrapped", False):
        return
    original = views.market_table

    def wrapped(lines, game):
        table = original(lines, game)
        age = market_freshness(lines)
        if age:
            table.note = f"{table.note} · fetched {age} ago" if table.note else f"fetched {age} ago"
        return table

    wrapped._freshness_wrapped = True  # type: ignore[attr-defined]
    views.market_table = wrapped


ZAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_url_blocks (
 url TEXT PRIMARY KEY,
 reason TEXT NOT NULL DEFAULT 'manual_zap',
 created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS content_url_block_insert
BEFORE INSERT ON content_items
WHEN EXISTS (
 SELECT 1 FROM content_url_blocks b
 WHERE b.url = NEW.canonical_url OR b.url = NEW.original_url
)
BEGIN
 SELECT RAISE(IGNORE);
END;
CREATE TRIGGER IF NOT EXISTS content_url_block_update
BEFORE UPDATE OF canonical_url,original_url ON content_items
WHEN EXISTS (
 SELECT 1 FROM content_url_blocks b
 WHERE b.url = NEW.canonical_url OR b.url = NEW.original_url
)
BEGIN
 SELECT RAISE(IGNORE);
END;
"""


def ensure_content_zap_schema(content_repository) -> None:
    content_repository.initialize()
    with closing(content_repository._connect()) as connection:
        connection.executescript(ZAP_SCHEMA)
        connection.commit()


def zap_content_url(content_repository, url: str) -> dict[str, Any]:
    """Block and delete content whose stored URL exactly matches ``url``."""
    target = str(url or "").strip()
    if not target.startswith(("http://", "https://")):
        raise ValueError("A full http(s) URL is required")
    ensure_content_zap_schema(content_repository)
    now = datetime.now(timezone.utc).isoformat()
    with closing(content_repository._connect()) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO content_url_blocks(url,reason,created_at) VALUES(?,?,?)",
            (target, "manual_exact_url_zap", now),
        )
        ids = {row[0] for row in connection.execute(
            """SELECT content_id FROM content_items
               WHERE canonical_url=? OR original_url=?
               UNION
               SELECT content_id FROM content_links WHERE url=?""",
            (target, target, target),
        )}
        story_ids: set[int] = set()
        if ids:
            marks = ",".join("?" for _ in ids)
            story_ids = {row[0] for row in connection.execute(
                f"SELECT DISTINCT story_id FROM story_items WHERE content_id IN ({marks})",
                tuple(ids),
            )}
            connection.execute(
                f"DELETE FROM content_items WHERE content_id IN ({marks})",
                tuple(ids),
            )
            if story_ids:
                story_marks = ",".join("?" for _ in story_ids)
                connection.execute(
                    f"""DELETE FROM stories
                        WHERE story_id IN ({story_marks})
                        AND NOT EXISTS (
                          SELECT 1 FROM story_items si WHERE si.story_id=stories.story_id
                        )""",
                    tuple(story_ids),
                )
                for story_id in story_ids:
                    connection.execute(
                        """UPDATE stories SET primary_content_id=(
                             SELECT content_id FROM story_items
                             WHERE story_id=? ORDER BY is_primary DESC,content_id LIMIT 1
                           ) WHERE story_id=? AND primary_content_id IS NULL""",
                        (story_id, story_id),
                    )
        connection.commit()
    return {
        "url": target,
        "removed_content": len(ids),
        "affected_stories": len(story_ids),
        "blocked": True,
    }
