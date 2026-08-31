"""Per-attempt passing detail from CFBD, and the middle/outside splits it enables.

CFBD publishes where a pass went -- left, middle or right, short or deep -- along
with air yards and yards after catch, one row per attempt. Before this the same
fields were recovered by parsing play text, which reached 1.4% of attempts; the
endpoint reaches 94-96% once a season has coverage.

Every row carries the provider's own play id, and those match `cfb_plays`
exactly, so our in-house ep-v2 EPA attaches to an attempt without any name or
clock matching. That join is the point of storing the attempts rather than the
provider's season aggregates: the aggregates carry air yards and yards after
catch but no direction split, and no EPA at all.

Coverage is uneven and the caller is expected to say so. 2025 has nothing before
week 8 and 90-96% from week 9; 2026 onward should be near-complete as games are
played. Every aggregate here reports how many attempts it is built from, because
a middle-of-the-field EPA over four throws is not a number anyone should read.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable

from sports_aggregator.cfb.repository import CFBRepository, schema_once


#: The EPA model the report and the matchup page read. See `pbp_cli`.
MODEL_VERSION = "ep-v2"

#: Below this an attempt split is noise, not a tendency. A team throws to the
#: middle roughly a fifth of the time, so a game gives single figures and only a
#: season's worth of attempts supports a comparison.
MIN_GAME_ATTEMPTS = 4
MIN_SEASON_ATTEMPTS = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS cfbd_passing_plays (
  play_id TEXT PRIMARY KEY,
  game_id INTEGER NOT NULL,
  season INTEGER NOT NULL,
  week INTEGER,
  offense TEXT NOT NULL,
  defense TEXT NOT NULL,
  passer TEXT,
  passer_id TEXT,
  target TEXT,
  target_id TEXT,
  down INTEGER,
  distance INTEGER,
  start_yards_to_goal INTEGER,
  pass_direction TEXT,
  pass_depth TEXT,
  pass_location TEXT,
  air_yards REAL,
  yards_after_catch REAL,
  total_yards REAL,
  outcome TEXT,
  parse_status TEXT,
  imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_passing_plays_game ON cfbd_passing_plays(game_id);
CREATE INDEX IF NOT EXISTS idx_passing_plays_offense ON cfbd_passing_plays(season, offense);
CREATE INDEX IF NOT EXISTS idx_passing_plays_defense ON cfbd_passing_plays(season, defense);
CREATE INDEX IF NOT EXISTS idx_passing_plays_passer ON cfbd_passing_plays(season, passer_id);
"""


@schema_once("passing_plays")
def initialize(repository: CFBRepository) -> None:
    # The splits below join to cfb_play_epa and cfb_play_metrics, so the modules
    # that own those tables are initialized here too. Declaring the dependency
    # is what `team_game_advanced` does, and it keeps a first read on a fresh
    # database from failing on a table nobody has created yet.
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_epa
    from sports_aggregator.cfb.play_by_play import initialize as initialize_plays
    repository.initialize()
    initialize_plays(repository)
    initialize_epa(repository)
    with closing(repository._connect()) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _row(item: dict[str, Any], now: str) -> tuple | None:
    play_id = item.get("playId")
    game_id = item.get("gameId")
    if play_id is None or game_id is None:
        return None
    offense = item.get("offense")
    defense = item.get("defense")
    if not offense or not defense:
        return None

    def identifier(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    return (
        str(play_id), int(game_id), int(item.get("season") or 0), item.get("week"),
        str(offense), str(defense), item.get("passer"), identifier(item.get("passerId")),
        item.get("target"), identifier(item.get("targetId")),
        item.get("down"), item.get("distance"), item.get("startYardsToGoal"),
        item.get("passDirection"), item.get("passDepth"), item.get("passLocation"),
        item.get("airYards"), item.get("yardsAfterCatch"), item.get("totalYards"),
        item.get("outcome"), item.get("parseStatus"), now,
    )


def store_attempts(repository: CFBRepository, attempts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Upsert attempts. Re-running a week corrects rows rather than duplicating."""
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    rows = [row for row in (_row(item, now) for item in attempts) if row]
    if not rows:
        return {"stored": 0, "classified": 0}
    with repository.transaction() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO cfbd_passing_plays VALUES (%s)" % ",".join("?" * 22), rows)
    classified = sum(1 for row in rows if row[13])
    return {"stored": len(rows), "classified": classified,
            "coverage": round(classified / len(rows), 4)}


def sync_week(repository: CFBRepository, client, *, season: int, week: int,
              force: bool = False) -> dict[str, Any]:
    raw = client.get("/passing/plays", {"year": int(season), "week": int(week)}, force=force)
    result = store_attempts(repository, raw)
    result.update({"season": int(season), "week": int(week)})
    return result


def sync_season(repository: CFBRepository, client, *, season: int,
                weeks: Iterable[int] | None = None, force: bool = False) -> dict[str, Any]:
    """Every week of a season. The endpoint rejects a season-wide request."""
    totals = {"season": int(season), "stored": 0, "classified": 0, "weeks": [], "failures": []}
    for week in (weeks if weeks is not None else range(1, 17)):
        try:
            result = sync_week(repository, client, season=season, week=int(week), force=force)
        except Exception as exc:  # noqa: BLE001 - one bad week must not lose the rest
            totals["failures"].append({"week": int(week), "error": str(exc)[:200]})
            continue
        totals["stored"] += result["stored"]
        totals["classified"] += result["classified"]
        totals["weeks"].append(result)
    attempts = totals["stored"]
    totals["coverage"] = round(totals["classified"] / attempts, 4) if attempts else 0.0
    return totals


#: One bucket of attempts: how many, what they were worth, and how they got there.
_BUCKET_SQL = """
  SELECT p.pass_direction AS direction,
         COUNT(*) AS attempts,
         SUM(CASE WHEN p.outcome='completion' THEN 1 ELSE 0 END) AS completions,
         AVG(e.epa) AS epa_per_attempt,
         SUM(e.epa) AS total_epa,
         AVG(p.air_yards) AS adot,
         AVG(p.yards_after_catch) AS yac,
         SUM(CASE WHEN p.air_yards IS NOT NULL THEN 1 ELSE 0 END) AS air_yards_available,
         SUM(CASE WHEN p.yards_after_catch IS NOT NULL THEN 1 ELSE 0 END) AS yac_available
  FROM cfbd_passing_plays p
  JOIN cfb_play_epa e ON e.play_id = p.play_id AND e.model_version = ?
  LEFT JOIN cfb_play_metrics m ON m.play_id = p.play_id
  WHERE p.pass_direction IS NOT NULL AND e.epa IS NOT NULL
    AND COALESCE(m.garbage_time, 0) = 0
    AND {scope}
  GROUP BY p.pass_direction
"""


def _buckets(connection, scope: str, params: tuple) -> dict[str, Any]:
    rows = connection.execute(_BUCKET_SQL.format(scope=scope), params).fetchall()
    middle = {"attempts": 0, "completions": 0, "total_epa": 0.0,
              "air_yards_available": 0, "yac_available": 0, "adot": None, "yac": None}
    outside = dict(middle)
    for row in rows:
        target = middle if row["direction"] == "middle" else outside
        weight = row["attempts"]
        target["attempts"] += weight
        target["completions"] += row["completions"] or 0
        target["total_epa"] += row["total_epa"] or 0.0
        target["air_yards_available"] += row["air_yards_available"] or 0
        target["yac_available"] += row["yac_available"] or 0
        for key, value in (("adot", row["adot"]), ("yac", row["yac"])):
            if value is None:
                continue
            # Weighted by attempts so left and right combine honestly.
            current = target[key]
            target[key] = value * weight if current is None else current + value * weight

    def finish(bucket: dict[str, Any]) -> dict[str, Any]:
        attempts = bucket["attempts"]
        if not attempts:
            return {"attempts": 0, "epa_per_attempt": None, "completion_rate": None,
                    "adot": None, "yac": None, "air_yards_available": 0, "yac_available": 0}
        return {
            "attempts": attempts,
            "epa_per_attempt": bucket["total_epa"] / attempts,
            "completion_rate": bucket["completions"] / attempts,
            "adot": (bucket["adot"] / attempts) if bucket["adot"] is not None else None,
            "yac": (bucket["yac"] / attempts) if bucket["yac"] is not None else None,
            "air_yards_available": bucket["air_yards_available"],
            "yac_available": bucket["yac_available"],
        }

    done_middle, done_outside = finish(middle), finish(outside)
    total = done_middle["attempts"] + done_outside["attempts"]
    return {"middle": done_middle, "outside": done_outside,
            "attempts": total,
            "middle_share": (done_middle["attempts"] / total) if total else None}


def game_splits(repository: CFBRepository, game_id: int, *,
                model_version: str = MODEL_VERSION) -> dict[str, dict[str, Any]]:
    """Middle/outside passing for both teams in one game, thrown and allowed."""
    initialize(repository)
    teams: dict[str, dict[str, Any]] = {}
    with repository._reader() as connection:
        sides = connection.execute(
            "SELECT DISTINCT offense, defense FROM cfbd_passing_plays WHERE game_id=?",
            (int(game_id),)).fetchall()
        for side in sides:
            for team, role in ((side["offense"], "offense"), (side["defense"], "defense")):
                entry = teams.setdefault(str(team), {})
                if role in entry:
                    continue
                column = "p.offense" if role == "offense" else "p.defense"
                entry[role] = _buckets(
                    connection, f"p.game_id = ? AND {column} = ?",
                    (model_version, int(game_id), str(team)))
    return teams


def team_season_splits(repository: CFBRepository, team: str, season: int, *,
                       model_version: str = MODEL_VERSION,
                       through_week: int | None = None) -> dict[str, Any]:
    """Season-to-date middle/outside passing for one team, thrown and allowed."""
    initialize(repository)
    bounds = "" if through_week is None else " AND p.week <= ?"
    extra: tuple = () if through_week is None else (int(through_week),)
    with repository._reader() as connection:
        offense = _buckets(
            connection, f"p.season = ? AND p.offense = ?{bounds}",
            (model_version, int(season), str(team), *extra))
        defense = _buckets(
            connection, f"p.season = ? AND p.defense = ?{bounds}",
            (model_version, int(season), str(team), *extra))
    return {"team": team, "season": int(season), "offense": offense, "defense": defense}


def coverage(repository: CFBRepository, season: int) -> dict[str, Any]:
    """How much of a season is classified, for callers that must disclose it."""
    initialize(repository)
    with repository._reader() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS attempts,
                      SUM(CASE WHEN pass_direction IS NOT NULL THEN 1 ELSE 0 END) AS classified,
                      COUNT(DISTINCT game_id) AS games,
                      MIN(week) AS first_week, MAX(week) AS last_week
               FROM cfbd_passing_plays WHERE season=?""", (int(season),)).fetchone()
    attempts = row["attempts"] or 0
    classified = row["classified"] or 0
    return {"season": int(season), "attempts": attempts, "classified": classified,
            "games": row["games"] or 0, "first_week": row["first_week"],
            "last_week": row["last_week"],
            "coverage": round(classified / attempts, 4) if attempts else 0.0}
