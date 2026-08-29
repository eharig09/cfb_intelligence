"""Reliable ESPN injury sync built on top of the conservative injury parser.

CFBD player IDs are commonly the same athlete IDs ESPN uses. Prefer that stable
identity over extracting a generic numeric ``id`` from ESPN search payloads.
College athlete-news/search feeds are incomplete, so the ESPN athlete web page
is also used as a conservative discovery surface for ESPN story links.
"""

from __future__ import annotations

from html import unescape
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from sports_aggregator.cfb.player_injuries import (
    NEWS_URL,
    SEARCH_URLS,
    USER_AGENT,
    _attributed_injury_clause,
    _json,
    _search_injury_articles,
    _store_rows,
    _walk,
    initialize,
    parse_injury_articles,
)

PLAYER_PAGE_URL = "https://www.espn.com/college-football/player/_/id/{athlete_id}"


def _positive_numeric_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else None


def _athlete_id_from_item(item: dict[str, Any]) -> str | None:
    """Extract only IDs that are explicitly identified as athlete identities."""
    direct = _positive_numeric_id(item.get("athleteId"))
    if direct:
        return direct

    uid = str(item.get("uid") or "")
    match = re.search(r"(?:^|~)a:(\d+)(?:$|~)", uid)
    if match:
        return match.group(1)

    for key in ("$ref", "href", "url", "link"):
        value = str(item.get(key) or "")
        match = re.search(r"/athletes/(\d+)(?:[/?#]|$)", value)
        if match:
            return match.group(1)

    kind = " ".join(
        str(item.get(key) or "") for key in ("type", "subtype", "entityType")
    ).casefold()
    if "athlete" in kind or "player" in kind:
        return _positive_numeric_id(item.get("id"))
    return None


def _name_of(item: dict[str, Any]) -> str:
    for key in ("displayName", "fullName", "name", "title", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_espn_athlete(player_name: str, preferred_id: Any = None) -> str | None:
    """Prefer the CFBD athlete identity, then conservatively resolve by name."""
    preferred = _positive_numeric_id(preferred_id)
    if preferred:
        return preferred

    wanted = " ".join(str(player_name).split()).casefold()
    query = quote_plus(player_name)
    for template in SEARCH_URLS:
        try:
            payload = _json(template.format(query=query))
        except Exception:
            continue
        for item in _walk(payload):
            if _name_of(item).casefold() != wanted:
                continue
            athlete_id = _athlete_id_from_item(item)
            if athlete_id:
                return athlete_id
    return None


def _text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=15) as response:  # nosec - ESPN hosts only
        return response.read().decode("utf-8", errors="replace")


def _meta(html: str, *, property_name: str | None = None,
          name: str | None = None) -> str | None:
    key = "property" if property_name else "name"
    value = property_name or name
    if not value:
        return None
    patterns = (
        rf'<meta[^>]+{key}=["\']{re.escape(value)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{key}=["\']{re.escape(value)}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return unescape(match.group(1)).strip()
    return None


def _story_links(html: str) -> list[str]:
    """Collect canonical ESPN story links exposed on an athlete page."""
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'href=["\']([^"\']*?/story/_/id/\d+[^"\']*)', html, re.I):
        href = unescape(match.group(1))
        url = urljoin("https://www.espn.com", href).split("?")[0]
        if "espn.com" in url.casefold() and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _article_from_page(url: str) -> dict[str, Any] | None:
    try:
        html = _text(url)
    except Exception:
        return None
    title = _meta(html, property_name="og:title")
    description = _meta(html, property_name="og:description") or _meta(html, name="description")
    if not title:
        title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        title = unescape(title_match.group(1)).strip() if title_match else None
    published = _meta(html, property_name="article:published_time")
    if not published:
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        published = match.group(1) if match else None
    if not title:
        return None
    return {
        "headline": title,
        "description": description or "",
        "link": url,
        "published": published,
    }


def _injury_year(text: str, default: int, player_name: str) -> int:
    """Use a historical year only when it is in the player's injury clause."""
    clause = _attributed_injury_clause(text, player_name)
    if not clause:
        return int(default)
    years = [int(value) for value in re.findall(r"\b(20\d{2})\b", clause)]
    years = [value for value in years if 2000 <= value <= int(default)]
    return years[-1] if years else int(default)


def _player_page_injury_articles(
    athlete_id: str, player_name: str, season: int, *, limit: int = 12
) -> list[dict[str, Any]]:
    """Follow recent ESPN story links from an athlete page and parse injuries."""
    try:
        page = _text(PLAYER_PAGE_URL.format(athlete_id=athlete_id))
    except Exception:
        return []

    articles: list[dict[str, Any]] = []
    for url in _story_links(page)[:limit]:
        article = _article_from_page(url)
        if article:
            articles.append(article)

    rows = parse_injury_articles(
        articles,
        fallback_season=season,
        required_player_name=player_name,
    )
    by_url = {article.get("link"): article for article in articles}
    for row in rows:
        article = by_url.get(row.get("source_url")) or {}
        evidence = " ".join(
            str(article.get(key) or "") for key in ("headline", "description")
        )
        row["season"] = _injury_year(
            evidence, int(row.get("season") or season), player_name
        )
    return rows


def sync_player(repository, player: dict[str, Any], season: int) -> dict[str, Any]:
    """Sync one veteran using stable athlete identity plus conservative evidence."""
    initialize(repository)
    stints = player.get("stints") or []
    if len({int(row.get("season")) for row in stints if row.get("season") is not None}) < 2:
        return {"player_id": player.get("player_id"), "skipped": "new_recruit", "stored": 0}

    player_name = str(player.get("name") or "").strip()
    athlete_id = resolve_espn_athlete(player_name, player.get("player_id"))
    rows: list[dict[str, Any]] = []
    news_status = "not_resolved"

    if athlete_id:
        try:
            payload = _json(NEWS_URL.format(athlete_id=athlete_id))
            rows.extend(parse_injury_articles(
                payload,
                fallback_season=season,
                required_player_name=player_name,
            ))
            news_status = "ok"
        except HTTPError as exc:
            news_status = f"http_{exc.code}"
        except Exception as exc:
            news_status = type(exc).__name__

    fallback_rows = _search_injury_articles(player_name, season)
    page_rows = (
        _player_page_injury_articles(athlete_id, player_name, season)
        if athlete_id else []
    )

    seen = {row["source_url"] for row in rows}
    for candidate in fallback_rows + page_rows:
        if candidate["source_url"] not in seen:
            seen.add(candidate["source_url"])
            rows.append(candidate)

    stored = _store_rows(repository, str(player["player_id"]), athlete_id, rows)
    return {
        "player_id": player.get("player_id"),
        "espn_athlete_id": athlete_id,
        "stored": stored,
        "athlete_news_status": news_status,
        "search_matches": len(fallback_rows),
        "player_page_matches": len(page_rows),
        "skipped": None if rows else "no_injury_evidence",
    }
