"""Wikipedia-backed FBS coordinator ingestion.

Season-team articles commonly expose OC/DC fields in the college-football team
infobox. Wikipedia is used as the production-safe baseline because some compact
staff-board sites block cloud-hosted requests. Source URLs are retained per row
for later official-school verification.

The MediaWiki API is queried in batches rather than once per team. That keeps a
full FBS season to only a handful of requests and avoids cloud-host rate limits.
"""

from __future__ import annotations

from contextlib import closing
import html as html_lib
import re
import time
from typing import Any, Iterable
from urllib.parse import quote

import requests

from sports_aggregator.cfb.coordinators import initialize, store_rows


API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "cfb-intelligence/1.0 (coordinator research; contact via project repository)"
BATCH_SIZE = 25
MAX_RETRIES = 4

# CFBD display names occasionally differ from the season-page title Wikipedia uses.
WIKI_SCHOOL_ALIASES = {
    "App State": "Appalachian State",
    "Florida International": "FIU",
    "Miami (OH)": "Miami",
    "UL Monroe": "Louisiana–Monroe",
    "UConn": "UConn",
}


def _clean_wikitext(value: str | None) -> str:
    text = str(value or "")
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.I | re.S)
    text = re.sub(r"<ref\b[^>]*/>", "", text, flags=re.I)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"''+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip(" ,;\n\t")


def _infobox_value(wikitext: str, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        match = re.search(rf"(?im)^\s*\|\s*{re.escape(key)}\s*=\s*(.*?)\s*$", wikitext)
        if match:
            value = _clean_wikitext(match.group(1))
            if value:
                return value
    return None


def parse_coordinators(wikitext: str) -> dict[str, str | None]:
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


def _candidate_title(team: dict[str, Any], season: int) -> str:
    school = str(team.get("school") or "").strip()
    school = WIKI_SCHOOL_ALIASES.get(school, school)
    mascot = str(team.get("mascot") or "").strip()
    if school and mascot:
        return f"{season} {school} {mascot} football team"
    return f"{season} {school} football team"


def _request_json(client: requests.Session, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    """GET MediaWiki JSON with conservative 429/5xx retry/backoff."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        response = client.get(
            API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else (2 ** attempt)
            except ValueError:
                delay = float(2 ** attempt)
            last_error = requests.HTTPError(
                f"{response.status_code} from Wikipedia; retrying in {delay:g}s",
                response=response,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(min(delay, 20.0))
                continue
        response.raise_for_status()
        return response.json()
    if last_error:
        raise last_error
    raise RuntimeError("Wikipedia request failed")


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _revision_text(page: dict[str, Any]) -> str | None:
    revisions = page.get("revisions") or []
    if not revisions:
        return None
    slots = revisions[0].get("slots") or {}
    main = slots.get("main") or {}
    text = main.get("content")
    return str(text) if text else None


def _batch_pages(client: requests.Session, requested: list[tuple[str, dict[str, Any]]],
                 timeout: float) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Fetch up to BATCH_SIZE season pages in one MediaWiki request."""
    originals = {title: team for title, team in requested}
    payload = _request_json(
        client,
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": "|".join(originals),
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        },
        timeout,
    )
    query = payload.get("query") or {}

    # MediaWiki may normalize punctuation/capitalization or follow a redirect.
    alias_to_original = {title: title for title in originals}
    for item in query.get("normalized") or []:
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if source in alias_to_original and target:
            alias_to_original[target] = alias_to_original[source]
    for item in query.get("redirects") or []:
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        original = alias_to_original.get(source, source)
        if target:
            alias_to_original[target] = original

    found: dict[int, dict[str, Any]] = {}
    seen_team_ids: set[int] = set()
    for page in query.get("pages") or []:
        if page.get("missing") is True:
            continue
        canonical = str(page.get("title") or "")
        original = alias_to_original.get(canonical, canonical)
        team = originals.get(original)
        if team is None:
            continue
        text = _revision_text(page)
        if not text:
            continue
        parsed = parse_coordinators(text)
        if not parsed["offense"] and not parsed["defense"]:
            continue
        team_id = int(team["team_id"])
        seen_team_ids.add(team_id)
        found[team_id] = {"title": canonical, **parsed}

    missing = [team for _, team in requested if int(team["team_id"]) not in seen_team_ids]
    return found, missing


def _search_title(client: requests.Session, team: dict[str, Any], season: int,
                  timeout: float) -> str | None:
    query = f'"{season}" "{team.get("school")}" football team'
    payload = _request_json(
        client,
        {
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": 0, "srlimit": 5, "format": "json", "formatversion": 2,
        },
        timeout,
    )
    prefix = f"{season} "
    for result in ((payload.get("query") or {}).get("search") or []):
        title = str(result.get("title") or "")
        if title.startswith(prefix) and "football" in title.lower():
            return title
    return None


def _single_title_page(client: requests.Session, title: str, timeout: float) -> dict[str, Any] | None:
    found, _ = _batch_pages(client, [(title, {"team_id": -1, "school": "", "mascot": ""})], timeout)
    # The synthetic team id is used only to reuse the batched query path.
    return found.get(-1)


def _mark_wikipedia_source(repository, season: int, team_id: int, source_url: str) -> None:
    with closing(repository._connect()) as connection:
        connection.execute(
            """UPDATE coordinator_seasons
               SET source_name='Wikipedia', source_url=?
               WHERE season=? AND team_id=?""",
            (source_url, int(season), int(team_id)),
        )
        connection.commit()


def _store_result(repository, season: int, team: dict[str, Any], result: dict[str, Any]) -> int:
    title = result.get("title")
    if not title:
        return 0
    source_url = "https://en.wikipedia.org/wiki/" + quote(
        str(title).replace(" ", "_"), safe="_()-,'"
    )
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
        return 0
    report = store_rows(repository, int(season), source_url, source_rows)
    _mark_wikipedia_source(repository, int(season), int(team["team_id"]), source_url)
    return int(report.get("stored") or 0)


def sync_season_wikipedia(repository, season: int, *, timeout: float = 20.0,
                          session: requests.Session | None = None) -> dict[str, Any]:
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
    failures: list[str] = []
    unresolved: list[dict[str, Any]] = []

    # Primary path: 25 team pages per request. A 136-team season is ~6 calls,
    # versus 136+ calls in the original implementation.
    requested = [(_candidate_title(team, int(season)), team) for team in teams]
    for batch in _chunks(requested, BATCH_SIZE):
        try:
            found, missing = _batch_pages(client, batch, timeout)
        except Exception as exc:
            failures.append(f"batch: {exc}")
            unresolved.extend(team for _, team in batch)
            continue
        for _, team in batch:
            result = found.get(int(team["team_id"]))
            if result:
                stored += _store_result(repository, int(season), team, result)
        unresolved.extend(missing)
        # Be courteous even though batching already cuts request volume sharply.
        time.sleep(0.2)

    # Only unusual title mismatches use search. Throttle these individually.
    missing_names: list[str] = []
    for team in unresolved:
        try:
            title = _search_title(client, team, int(season), timeout)
            if not title:
                missing_names.append(str(team["school"]))
                continue
            result = _single_title_page(client, title, timeout)
            if not result:
                missing_names.append(str(team["school"]))
                continue
            stored += _store_result(repository, int(season), team, result)
        except Exception as exc:
            failures.append(f"{team['school']}: {exc}")
        time.sleep(0.4)

    return {
        "season": int(season),
        "source": "Wikipedia",
        "teams": len(teams),
        "stored": stored,
        "missing": sorted(set(missing_names)),
        "failures": failures,
        "unresolved": [],
    }
