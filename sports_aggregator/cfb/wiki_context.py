"""Slow-changing Wikipedia context for college-football program identity.

The live application keeps this data in SQLite. A team page may populate a
missing/stale cache entry, but ordinary renders read the cached row; Wikipedia
is never required for the page to succeed.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import html
import json
import re
from typing import Any

import requests

WIKI_API = "https://en.wikipedia.org/w/api.php"
CACHE_DAYS = 90
FAILURE_RETRY_HOURS = 12
USER_AGENT = "cfb-intelligence/1.0 (college-football historical context)"

_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS team_wiki_context (
    team_id INTEGER PRIMARY KEY,
    school TEXT NOT NULL,
    page_title TEXT,
    page_url TEXT,
    first_season INTEGER,
    national_championships TEXT,
    conference_championships TEXT,
    mascot TEXT,
    summary TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    fetched_at TEXT NOT NULL,
    fetch_ok INTEGER NOT NULL DEFAULT 1
)
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _strip_markup(value: str | None) -> str | None:
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", "", text, flags=re.S | re.I)
    text = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
    for _ in range(3):
        text = re.sub(r"\{\{(?:nowrap|small|nobold|plainlist)\|([^{}]+)\}\}", r"\1", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"\s+", " ", text).strip(" |,;")
    return text or None


def _infobox_fields(wikitext: str) -> dict[str, str]:
    match = re.search(r"\{\{Infobox college football team\b", wikitext, flags=re.I)
    if not match:
        return {}
    start = match.start()
    depth = 0
    end = None
    index = start
    while index < len(wikitext) - 1:
        pair = wikitext[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                end = index
                break
            continue
        index += 1
    block = wikitext[start:end] if end else wikitext[start:start + 12000]
    fields: dict[str, str] = {}
    current_key = None
    current_parts: list[str] = []
    nested = 0
    for line in block.splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("|") and nested == 0 and "=" in stripped:
            if current_key:
                fields[current_key] = "\n".join(current_parts).strip()
            key, value = stripped[1:].split("=", 1)
            current_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
            current_parts = [value.strip()]
        elif current_key:
            current_parts.append(stripped)
        nested += line.count("{{") - line.count("}}")
        nested = max(0, nested)
    if current_key:
        fields[current_key] = "\n".join(current_parts).strip()
    return fields


def _first(fields: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = fields.get(re.sub(r"[^a-z0-9]+", "", key.casefold()))
        cleaned = _strip_markup(value)
        if cleaned:
            return cleaned
    return None


def _first_season(fields: dict[str, str]) -> int | None:
    value = _first(fields, "firstyear", "firstseason", "first_year", "first season")
    if not value:
        return None
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", value)
    return int(match.group(1)) if match else None


def _article_search(session: requests.Session, school: str, mascot: str | None) -> str | None:
    queries = []
    if mascot:
        queries.append(f'"{school} {mascot} football"')
    queries.extend((f'"{school} football"', f'{school} football'))
    for query in queries:
        response = session.get(WIKI_API, params={
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": 0, "srlimit": 5, "format": "json", "formatversion": 2,
        }, timeout=4)
        response.raise_for_status()
        for row in response.json().get("query", {}).get("search", []):
            title = str(row.get("title") or "")
            if "football" in title.casefold():
                return title
    return None


def _fetch_wikipedia(school: str, mascot: str | None) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    title = _article_search(session, school, mascot)
    if not title:
        raise LookupError(f"No Wikipedia football article found for {school}")

    query = session.get(WIKI_API, params={
        "action": "query", "prop": "extracts|info", "titles": title,
        "exintro": 1, "explaintext": 1, "inprop": "url",
        "redirects": 1, "format": "json", "formatversion": 2,
    }, timeout=4)
    query.raise_for_status()
    pages = query.json().get("query", {}).get("pages", [])
    page = pages[0] if pages else {}

    parsed = session.get(WIKI_API, params={
        "action": "parse", "page": page.get("title") or title,
        "prop": "wikitext", "format": "json", "formatversion": 2,
    }, timeout=4)
    parsed.raise_for_status()
    wikitext = parsed.json().get("parse", {}).get("wikitext", "") or ""
    fields = _infobox_fields(wikitext)

    return {
        "page_title": page.get("title") or title,
        "page_url": page.get("fullurl"),
        "first_season": _first_season(fields),
        "national_championships": _first(fields, "natltitles", "nationaltitles", "nationalchampionships"),
        "conference_championships": _first(fields, "conftitles", "conferencetitles", "conferencechampionships"),
        "mascot": _first(fields, "mascotdisplay", "mascot", "nickname"),
        "summary": (str(page.get("extract") or "").strip() or None),
        "raw_fields": fields,
    }


def _ensure_cache(repository) -> None:
    repository.initialize()
    with closing(repository._connect()) as connection:
        connection.execute(_CACHE_SQL)
        connection.commit()


def _read_cache(repository, team_id: int) -> dict[str, Any] | None:
    _ensure_cache(repository)
    with closing(repository._connect()) as connection:
        row = connection.execute("SELECT * FROM team_wiki_context WHERE team_id=?", (int(team_id),)).fetchone()
    return dict(row) if row is not None else None


def _write_cache(repository, team_id: int, school: str, payload: dict[str, Any], ok: bool) -> None:
    _ensure_cache(repository)
    with closing(repository._connect()) as connection:
        connection.execute(
            """INSERT INTO team_wiki_context(
                   team_id,school,page_title,page_url,first_season,
                   national_championships,conference_championships,mascot,summary,
                   payload_json,fetched_at,fetch_ok)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(team_id) DO UPDATE SET
                   school=excluded.school,page_title=excluded.page_title,page_url=excluded.page_url,
                   first_season=excluded.first_season,
                   national_championships=excluded.national_championships,
                   conference_championships=excluded.conference_championships,
                   mascot=excluded.mascot,summary=excluded.summary,
                   payload_json=excluded.payload_json,fetched_at=excluded.fetched_at,
                   fetch_ok=excluded.fetch_ok""",
            (
                int(team_id), school, payload.get("page_title"), payload.get("page_url"),
                payload.get("first_season"), payload.get("national_championships"),
                payload.get("conference_championships"), payload.get("mascot"), payload.get("summary"),
                json.dumps(payload, ensure_ascii=False, default=str), _now().isoformat(), 1 if ok else 0,
            ),
        )
        connection.commit()


def team_wiki_context(repository, team: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    team_id = int(team["team_id"])
    school = str(team.get("school") or "")
    cached = _read_cache(repository, team_id)
    fetched_at = _parse_dt((cached or {}).get("fetched_at"))
    ok = bool((cached or {}).get("fetch_ok"))
    fresh_for = timedelta(days=CACHE_DAYS) if ok else timedelta(hours=FAILURE_RETRY_HOURS)
    needs_refresh = refresh or cached is None or fetched_at is None or _now() - fetched_at > fresh_for

    if needs_refresh:
        try:
            payload = _fetch_wikipedia(school, team.get("mascot"))
            _write_cache(repository, team_id, school, payload, True)
            cached = _read_cache(repository, team_id)
        except Exception as exc:
            if cached and cached.get("fetch_ok"):
                preserved = dict(cached)
                preserved["error"] = str(exc)
                _write_cache(repository, team_id, school, preserved, True)
            else:
                _write_cache(repository, team_id, school, {"error": str(exc)}, False)
            cached = _read_cache(repository, team_id)

    result = dict(cached or {})
    try:
        result["payload"] = json.loads(result.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        result["payload"] = {}
    return result
