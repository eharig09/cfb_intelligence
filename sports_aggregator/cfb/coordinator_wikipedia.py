"""Wikipedia-backed FBS coordinator ingestion.

Season-team articles commonly expose OC/DC fields in the college-football team
infobox.  Wikipedia is used as the production-safe baseline because some compact
staff-board sites block cloud-hosted requests.  Source URLs are retained per row
for later official-school verification.
"""

from __future__ import annotations

from contextlib import closing
import html as html_lib
import re
from typing import Any
from urllib.parse import quote

import requests

from sports_aggregator.cfb.coordinators import initialize, store_rows


API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "cfb-intelligence/1.0 (coordinator research; contact via project repository)"


def _clean_wikitext(value: str | None) -> str:
    text = str(value or "")
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.I | re.S)
    text = re.sub(r"<ref\b[^>]*/>", "", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    # [[Target|Label]] -> Label, [[Target]] -> Target
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"''+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip(" ,;\n\t")


def _infobox_value(wikitext: str, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        match = re.search(
            rf"(?im)^\s*\|\s*{re.escape(key)}\s*=\s*(.*?)\s*$",
            wikitext,
        )
        if match:
            value = _clean_wikitext(match.group(1))
            if value:
                return value
    return None


def parse_coordinators(wikitext: str) -> dict[str, str | None]:
    """Extract the primary offensive and defensive coordinator names."""
    return {
        "offense": _infobox_value(
            wikitext,
            ("off_coach", "offcoach", "cooff_coach1", "co_off_coach1", "cooff_coach"),
        ),
        "defense": _infobox_value(
            wikitext,
            ("def_coach", "defcoach", "codef_coach1", "co_def_coach1", "codef_coach"),
        ),
    }


def _candidate_titles(team: dict[str, Any], season: int) -> list[str]:
    school = str(team.get("school") or "").strip()
    mascot = str(team.get("mascot") or "").strip()
    candidates = []
    if school and mascot:
        candidates.append(f"{season} {school} {mascot} football team")
    if school:
        candidates.append(f"{season} {school} football team")
    return candidates


def _page_wikitext(client: requests.Session, title: str, timeout: float) -> tuple[str, str] | None:
    response = client.get(
        API_URL,
        params={
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    parsed = payload.get("parse") or {}
    text = parsed.get("wikitext")
    canonical = parsed.get("title") or title
    if not text:
        return None
    return str(canonical), str(text)


def _search_title(client: requests.Session, query: str, season: int, timeout: float) -> str | None:
    response = client.get(
        API_URL,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 0,
            "srlimit": 5,
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    results = ((response.json().get("query") or {}).get("search") or [])
    prefix = f"{season} "
    for result in results:
        title = str(result.get("title") or "")
        if title.startswith(prefix) and "football" in title.lower():
            return title
    return None


def fetch_team_coordinators(client: requests.Session, team: dict[str, Any], season: int,
                            timeout: float = 20.0) -> dict[str, Any]:
    """Resolve one team's season page and extract primary OC/DC."""
    last_error = None
    for title in _candidate_titles(team, season):
        try:
            page = _page_wikitext(client, title, timeout)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {404, 400}:
                page = None
            else:
                raise
        except Exception as exc:  # malformed/nonexistent title can return API errors as JSON
            last_error = str(exc)
            page = None
        if page:
            canonical, text = page
            parsed = parse_coordinators(text)
            if parsed["offense"] or parsed["defense"]:
                return {"title": canonical, "wikitext": text, **parsed}

    query = f'"{season}" "{team.get("school")}" football team'
    title = _search_title(client, query, season, timeout)
    if title:
        page = _page_wikitext(client, title, timeout)
        if page:
            canonical, text = page
            parsed = parse_coordinators(text)
            return {"title": canonical, "wikitext": text, **parsed}
    return {"title": None, "offense": None, "defense": None, "error": last_error}


def sync_season_wikipedia(repository, season: int, *, timeout: float = 20.0,
                          session: requests.Session | None = None) -> dict[str, Any]:
    """Fetch primary OC/DC assignments for every stored FBS team in one season."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            """SELECT team_id,school,mascot,classification
               FROM teams
               WHERE lower(COALESCE(classification,''))='fbs'
               ORDER BY school"""
        ).fetchall()
    teams = [dict(row) for row in rows]
    client = session or requests.Session()
    stored = 0
    missing: list[str] = []
    failures: list[str] = []

    for team in teams:
        try:
            result = fetch_team_coordinators(client, team, int(season), timeout=timeout)
        except Exception as exc:
            failures.append(f"{team['school']}: {exc}")
            continue
        title = result.get("title")
        if not title:
            missing.append(str(team["school"]))
            continue
        source_url = "https://en.wikipedia.org/wiki/" + quote(str(title).replace(" ", "_"), safe="_()-,'")
        source_rows = []
        if result.get("offense"):
            source_rows.append({
                "team": team["school"], "side": "offense", "role": "OC",
                "coach_name": result["offense"], "rating": None, "experience_years": None,
            })
        if result.get("defense"):
            source_rows.append({
                "team": team["school"], "side": "defense", "role": "DC",
                "coach_name": result["defense"], "rating": None, "experience_years": None,
            })
        if not source_rows:
            missing.append(str(team["school"]))
            continue
        report = store_rows(repository, int(season), source_url, source_rows)
        stored += int(report.get("stored") or 0)

    return {
        "season": int(season),
        "source": "Wikipedia",
        "teams": len(teams),
        "stored": stored,
        "missing": missing,
        "failures": failures,
        "unresolved": [],
    }
