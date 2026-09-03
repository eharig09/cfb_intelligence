"""FBS offensive/defensive coordinator ingestion and persistence.

The baseline source is Punt & Rally's public coaching-staff boards.  They expose
all FBS programs on one offense board and one defense board for each season,
which is much cheaper and more reliable than maintaining 130+ school-specific
scrapers.  Source URLs are stored with every row so official-school verification
can be layered on later without changing the schema.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
import sqlite3
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.repository import schema_once


SOURCE_URL = "https://www.puntandrally.com/staff_ratings.php"
USER_AGENT = "cfb-intelligence/1.0 coordinator-research"

COORDINATOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS coordinator_seasons (
    season INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    team TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('offense','defense')),
    role TEXT NOT NULL CHECK(role IN ('OC','DC')),
    coach_name TEXT NOT NULL,
    rating REAL,
    experience_years INTEGER,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    verified_official INTEGER NOT NULL DEFAULT 0,
    official_source_url TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (season, team_id, side),
    FOREIGN KEY (team_id) REFERENCES teams(team_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_coordinator_team_season
    ON coordinator_seasons(team_id, season DESC);
CREATE INDEX IF NOT EXISTS idx_coordinator_name
    ON coordinator_seasons(coach_name, season DESC);
"""

# Public-source spelling/display differences that are not necessarily represented
# in CFBD's normal alias list. Keep this intentionally small; database aliases are
# still the primary resolver.
TEAM_ALIASES = {
    "App State": "Appalachian State",
    "Florida International": "FIU",
    "Hawai'i": "Hawai'i",
    "Miami (OH)": "Miami (OH)",
    "UL Monroe": "Louisiana Monroe",
    "UMass": "Massachusetts",
    "UConn": "Connecticut",
}


def board_url(season: int, side: str) -> str:
    side = side.lower().strip()
    if side not in {"offense", "defense"}:
        raise ValueError("side must be offense or defense")
    return f"{SOURCE_URL}?board={side}&year={int(season)}"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_board(html: str, side: str) -> list[dict[str, Any]]:
    """Parse a Punt & Rally coordinator board into normalized rows.

    The parser keys off the visible table headers rather than brittle CSS classes,
    so ordinary site redesigns that preserve the table remain compatible.
    """
    side = side.lower().strip()
    if side not in {"offense", "defense"}:
        raise ValueError("side must be offense or defense")
    role = "OC" if side == "offense" else "DC"
    soup = BeautifulSoup(html, "html.parser")
    wanted_header = role.lower()

    table = None
    headers: list[str] = []
    for candidate in soup.find_all("table"):
        candidate_headers = [_clean(cell.get_text(" ", strip=True)).lower()
                             for cell in candidate.find_all("th")]
        if "school" in candidate_headers and wanted_header in candidate_headers:
            table = candidate
            headers = candidate_headers
            break
    if table is None:
        raise ValueError(f"Could not find {role} coaching table")

    school_idx = headers.index("school")
    coach_idx = headers.index(wanted_header)
    rating_idx = headers.index("rating") if "rating" in headers else None
    years_idx = headers.index("yrs") if "yrs" in headers else None

    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(school_idx, coach_idx):
            continue
        school = _clean(cells[school_idx].get_text(" ", strip=True))
        # Conference abbreviations are often rendered in the same school cell.
        # Prefer link text, which is just the program display name.
        school_link = cells[school_idx].find("a")
        if school_link is not None:
            school = _clean(school_link.get_text(" ", strip=True))
        coach = _clean(cells[coach_idx].get_text(" ", strip=True)).rstrip("*").strip()
        if not school or not coach:
            continue

        rating = None
        if rating_idx is not None and rating_idx < len(cells):
            try:
                rating = float(_clean(cells[rating_idx].get_text(" ", strip=True)))
            except (TypeError, ValueError):
                pass
        years = None
        if years_idx is not None and years_idx < len(cells):
            match = re.search(r"-?\d+", _clean(cells[years_idx].get_text(" ", strip=True)))
            if match:
                years = int(match.group())

        rows.append({
            "team": school,
            "side": side,
            "role": role,
            "coach_name": coach,
            "rating": rating,
            "experience_years": years,
        })
    return rows


def fetch_board(season: int, side: str, *, timeout: float = 20.0,
                session: requests.Session | None = None) -> tuple[str, list[dict[str, Any]]]:
    url = board_url(season, side)
    client = session or requests.Session()
    response = client.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return url, parse_board(response.text, side)


def _resolve_team(connection: sqlite3.Connection, source_team: str) -> tuple[int, str] | None:
    candidates = [source_team]
    mapped = TEAM_ALIASES.get(source_team)
    if mapped and mapped not in candidates:
        candidates.append(mapped)

    for name in candidates:
        row = connection.execute(
            "SELECT team_id,school FROM teams WHERE lower(school)=lower(?) LIMIT 1", (name,)
        ).fetchone()
        if row is not None:
            return int(row[0]), str(row[1])
        row = connection.execute(
            """SELECT t.team_id,t.school
               FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
               WHERE a.normalized_alias=? LIMIT 1""",
            (normalize_alias(name),),
        ).fetchone()
        if row is not None:
            return int(row[0]), str(row[1])
    return None


#: A name that still carries template punctuation came from a parser reading
#: an infobox, not from a person. `624c1fc` stopped one being written; this
#: refuses it at the door, because the writer is not the only way a row gets
#: here and a deployment that synced before that fix has them stored.
_NOT_A_NAME = "coach_name LIKE '%|%' OR coach_name LIKE '%=%' OR coach_name LIKE '%{%'"


def is_plausible_name(value: Any) -> bool:
    """Whether this is a person's name rather than a fragment of a template."""
    text = str(value or "").strip()
    if not text or len(text) > 60:
        return False
    if any(character in text for character in "|={}[]"):
        return False
    return any(character.isalpha() for character in text)


@schema_once("coordinators")
def initialize(repository) -> None:
    repository.initialize()
    with closing(repository._connect()) as connection:
        connection.executescript(COORDINATOR_SCHEMA)
        # Any database synced before the parser was fixed holds wikitext where
        # the names should be -- `| oc_year =` was the most prolific offensive
        # coordinator in the country, and because the same string landed on
        # dozens of teams it read as one man holding five jobs in 2022. Clearing
        # them here means a deployment heals on its next start rather than
        # needing someone to know to go and delete them.
        connection.execute(f"DELETE FROM coordinator_seasons WHERE {_NOT_A_NAME}")
        connection.commit()


def store_rows(repository, season: int, source_url: str,
               rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Upsert one board and report teams that could not be mapped to CFBD."""
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    unresolved: list[str] = []
    with closing(repository._connect()) as connection:
        for item in rows:
            if not is_plausible_name(item.get("coach_name")):
                # Better a team with no coordinator on record than a team whose
                # coordinator is a line of markup.
                continue
            resolved = _resolve_team(connection, str(item.get("team") or ""))
            if resolved is None:
                unresolved.append(str(item.get("team") or ""))
                continue
            team_id, school = resolved
            connection.execute(
                """INSERT INTO coordinator_seasons (
                       season,team_id,team,side,role,coach_name,rating,experience_years,
                       source_name,source_url,verified_official,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(season,team_id,side) DO UPDATE SET
                       team=excluded.team,
                       role=excluded.role,
                       coach_name=excluded.coach_name,
                       rating=excluded.rating,
                       experience_years=excluded.experience_years,
                       source_name=excluded.source_name,
                       source_url=excluded.source_url,
                       updated_at=excluded.updated_at""",
                (int(season), team_id, school, item["side"], item["role"],
                 item["coach_name"], item.get("rating"), item.get("experience_years"),
                 "Punt & Rally", source_url, now),
            )
            stored += 1
        connection.commit()
    return {"stored": stored, "unresolved": sorted(set(filter(None, unresolved)))}


def sync_season(repository, season: int, *, timeout: float = 20.0,
                session: requests.Session | None = None) -> dict[str, Any]:
    """Fetch and store both coordinator boards for one season."""
    result: dict[str, Any] = {"season": int(season), "stored": 0, "unresolved": []}
    unresolved: set[str] = set()
    for side in ("offense", "defense"):
        url, rows = fetch_board(season, side, timeout=timeout, session=session)
        report = store_rows(repository, season, url, rows)
        result[side] = {"fetched": len(rows), **report, "source_url": url}
        result["stored"] += report["stored"]
        unresolved.update(report["unresolved"])
    result["unresolved"] = sorted(unresolved)
    return result


def coordinator_rows(repository, team_id: int, through_season: int | None = None) -> list[dict[str, Any]]:
    """Return coordinator lineage newest-first for a team."""
    initialize(repository)
    params: list[Any] = [int(team_id)]
    where = "team_id=?"
    if through_season is not None:
        where += " AND season<=?"
        params.append(int(through_season))
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            f"""SELECT season,team,side,role,coach_name,rating,experience_years,
                       source_name,source_url,verified_official,official_source_url
                FROM coordinator_seasons WHERE {where}
                ORDER BY season DESC, side""",
            params,
        ).fetchall()
    return [dict(row) for row in rows]
