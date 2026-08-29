"""Private CFBDepth CSV imports and read helpers.

The exported CSV rows are intentionally stored in SQLite rather than committed
as repository data. That keeps the third-party dataset separable from public
CFBD data and makes it straightforward to gate later.
"""

from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timezone
from io import StringIO, TextIOBase
from typing import Any, Iterable

from sports_aggregator.cfb.models import normalize_alias


SCHEMA = """
CREATE TABLE IF NOT EXISTS cfbdepth_roster_breakdown (
    school TEXT PRIMARY KEY,
    normalized_school TEXT NOT NULL,
    conference TEXT,
    active_players INTEGER,
    transfers INTEGER,
    transfer_pct REAL,
    home_grown INTEGER,
    home_grown_pct REAL,
    five_star INTEGER,
    four_star INTEGER,
    three_star INTEGER,
    two_star INTEGER,
    zero_star INTEGER,
    blue_chip_pct REAL,
    ol_avg_wt REAL,
    dl_avg_wt REAL,
    roster_avg_wt REAL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cfbdepth_roster_school
    ON cfbdepth_roster_breakdown(normalized_school);

CREATE TABLE IF NOT EXISTS cfbdepth_team_impact (
    school TEXT PRIMARY KEY,
    normalized_school TEXT NOT NULL,
    conference TEXT,
    injury_number INTEGER,
    injury_new INTEGER,
    ofs INTEGER,
    o INTEGER,
    d INTEGER,
    q INTEGER,
    p INTEGER,
    s INTEGER,
    gtd INTEGER,
    opt INTEGER,
    ret INTEGER,
    injury_impact REAL,
    impact_pp REAL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cfbdepth_impact_school
    ON cfbdepth_team_impact(normalized_school);

CREATE TABLE IF NOT EXISTS cfbdepth_player_updates (
    update_id INTEGER PRIMARY KEY AUTOINCREMENT,
    abbreviation TEXT,
    player_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    team TEXT NOT NULL,
    normalized_team TEXT NOT NULL,
    position TEXT,
    status TEXT,
    rating REAL,
    impact REAL,
    is_new INTEGER NOT NULL DEFAULT 0,
    last_update TEXT,
    update_text TEXT,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cfbdepth_updates_player
    ON cfbdepth_player_updates(normalized_name, normalized_team, last_update DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(repository) -> None:
    with closing(repository._connect()) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _percent(value: Any) -> float | None:
    return _number(value)


def _reader(source: str | bytes | TextIOBase) -> Iterable[dict[str, str]]:
    if isinstance(source, bytes):
        text = source.decode("utf-8-sig", errors="replace")
    elif isinstance(source, str):
        text = source
    else:
        text = source.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig", errors="replace")
    return csv.DictReader(StringIO(text))


def import_roster_breakdown(repository, source: str | bytes | TextIOBase) -> int:
    initialize(repository)
    now = _now_iso()
    rows = []
    for raw in _reader(source):
        school = str(raw.get("School") or "").strip()
        if not school:
            continue
        rows.append((
            school, normalize_alias(school), raw.get("Conference"),
            _integer(raw.get("Active Players")), _integer(raw.get("Transfers")),
            _percent(raw.get("Transfer%")), _integer(raw.get("Home Grown")),
            _percent(raw.get("Home Grown%")), _integer(raw.get("5-Star")),
            _integer(raw.get("4-Star")), _integer(raw.get("3-Star")),
            _integer(raw.get("2-Star")), _integer(raw.get("0-Star")),
            _percent(raw.get("Blue Chip%")), _number(raw.get("OL Avg Wt")),
            _number(raw.get("DL Avg Wt")), _number(raw.get("Roster Avg Wt")), now,
        ))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfbdepth_roster_breakdown")
        connection.executemany(
            """INSERT INTO cfbdepth_roster_breakdown VALUES(
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        connection.commit()
    return len(rows)


def import_team_impact(repository, source: str | bytes | TextIOBase) -> int:
    initialize(repository)
    now = _now_iso()
    rows = []
    for raw in _reader(source):
        school = str(raw.get("School") or "").strip()
        if not school:
            continue
        rows.append((
            school, normalize_alias(school), raw.get("Conference"),
            _integer(raw.get("Injury Number")), _integer(raw.get("Injury New")),
            _integer(raw.get("OFS")), _integer(raw.get("O")), _integer(raw.get("D")),
            _integer(raw.get("Q")), _integer(raw.get("P")), _integer(raw.get("S")),
            _integer(raw.get("GTD")), _integer(raw.get("OPT")), _integer(raw.get("RET")),
            _number(raw.get("Injury Impact")), _number(raw.get("Impact PP")), now,
        ))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfbdepth_team_impact")
        connection.executemany(
            """INSERT INTO cfbdepth_team_impact VALUES(
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        connection.commit()
    return len(rows)


def import_player_updates(repository, source: str | bytes | TextIOBase) -> int:
    initialize(repository)
    now = _now_iso()
    rows = []
    for raw in _reader(source):
        name = str(raw.get("Name") or "").strip()
        team = str(raw.get("Team") or "").strip()
        if not name or not team:
            continue
        is_new = str(raw.get("New") or "").strip().casefold() in {"1", "true", "yes", "y", "new"}
        rows.append((
            raw.get("Abb"), name, normalize_alias(name), team, normalize_alias(team),
            raw.get("Pos"), raw.get("Status"), _number(raw.get("Rating")),
            _number(raw.get("Impact")), int(is_new), raw.get("Last Update"),
            raw.get("Update"), now,
        ))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfbdepth_player_updates")
        connection.executemany(
            """INSERT INTO cfbdepth_player_updates(
               abbreviation,player_name,normalized_name,team,normalized_team,position,
               status,rating,impact,is_new,last_update,update_text,imported_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        connection.commit()
    return len(rows)


def roster_breakdown(repository, school: str) -> dict[str, Any] | None:
    initialize(repository)
    with closing(repository._connect()) as connection:
        row = connection.execute(
            "SELECT * FROM cfbdepth_roster_breakdown WHERE normalized_school=? LIMIT 1",
            (normalize_alias(school),),
        ).fetchone()
    return dict(row) if row else None


def team_impact(repository, school: str) -> dict[str, Any] | None:
    initialize(repository)
    with closing(repository._connect()) as connection:
        row = connection.execute(
            "SELECT * FROM cfbdepth_team_impact WHERE normalized_school=? LIMIT 1",
            (normalize_alias(school),),
        ).fetchone()
    return dict(row) if row else None


def player_updates(repository, player_name: str, team: str | None = None,
                   limit: int = 8) -> list[dict[str, Any]]:
    """Return only an exact-team match or a name that is unique to one source team."""
    initialize(repository)
    normalized_name = normalize_alias(player_name)
    with closing(repository._connect()) as connection:
        if team:
            rows = connection.execute(
                """SELECT * FROM cfbdepth_player_updates
                   WHERE normalized_name=? AND normalized_team=?
                   ORDER BY update_id DESC LIMIT ?""",
                (normalized_name, normalize_alias(team), int(limit)),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
        all_rows = [dict(row) for row in connection.execute(
            """SELECT * FROM cfbdepth_player_updates
               WHERE normalized_name=? ORDER BY update_id DESC""",
            (normalized_name,),
        ).fetchall()]
    if not all_rows:
        return []
    source_teams = {row["normalized_team"] for row in all_rows}
    if len(source_teams) != 1:
        return []
    return all_rows[: int(limit)]
