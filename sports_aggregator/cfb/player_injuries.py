"""Sourced historical injury annotations for CFB player career paths.

ESPN does not expose a reliable NCAAF injury-history collection comparable to
its NFL feed. This module instead resolves an ESPN college-football athlete,
reads that athlete's ESPN news, and stores only articles whose headline/summary
explicitly contains injury or surgery language. Absence from a game is never
classified as an injury by itself.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


SEARCH_URLS = (
    "https://site.web.api.espn.com/apis/search/v2?query={query}&sport=football&limit=12",
    "https://site.web.api.espn.com/apis/common/v3/search?query={query}&limit=12",
)
NEWS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/"
    "college-football/athletes/{athlete_id}/news"
)
USER_AGENT = "cfb-intelligence/1.0 injury-research"

INJURY_TERMS = re.compile(
    r"\b(injur(?:y|ed|ies)|hurt|surgery|surgical|torn|tear|sprain(?:ed)?|strain(?:ed)?|"
    r"fractur(?:e|ed)|break|broken|concussion|acl|mcl|achilles|hamstring|ankle|knee|"
    r"shoulder|foot|wrist|hand|elbow|back|hip|groin|leg|arm|neck|head)\b",
    re.I,
)
BODY_PARTS = (
    "ACL", "MCL", "Achilles", "concussion", "hamstring", "ankle", "knee",
    "shoulder", "foot", "wrist", "hand", "elbow", "back", "hip", "groin",
    "leg", "arm", "neck", "head",
)
SEASON_ENDING = re.compile(
    r"\b(season[- ]ending|out for (?:the )?(?:rest of the )?season|miss (?:the )?rest of (?:the )?season)\b",
    re.I,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_injury_events (
    player_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    espn_athlete_id TEXT,
    injury_label TEXT NOT NULL,
    body_part TEXT,
    details TEXT,
    season_ending INTEGER NOT NULL DEFAULT 0,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_published_at TEXT,
    confidence TEXT NOT NULL CHECK(confidence IN ('confirmed','reported')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, source_url)
);
CREATE INDEX IF NOT EXISTS idx_player_injury_events_player
    ON player_injury_events(player_id, season DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(repository) -> None:
    with closing(repository._connect()) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:  # nosec - fixed ESPN hosts only
        return json.loads(response.read().decode("utf-8"))


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _name_of(item: dict[str, Any]) -> str:
    for key in ("displayName", "fullName", "name", "title", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_espn_athlete(player_name: str) -> str | None:
    """Resolve an exact ESPN athlete ID from search, rejecting fuzzy names."""
    wanted = " ".join(str(player_name).split()).casefold()
    query = quote_plus(player_name)
    for template in SEARCH_URLS:
        try:
            payload = _json(template.format(query=query))
        except Exception:
            continue
        candidates: list[str] = []
        for item in _walk(payload):
            if _name_of(item).casefold() != wanted:
                continue
            raw_id = item.get("id") or item.get("athleteId") or item.get("uid")
            if raw_id is None:
                continue
            match = re.search(r"(?:a:)?(\d+)$", str(raw_id))
            if match:
                candidates.append(match.group(1))
        if candidates:
            return candidates[0]
    return None


def _article_url(article: dict[str, Any]) -> str | None:
    for key in ("link", "url", "href", "webUrl"):
        value = article.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    links = article.get("links")
    if isinstance(links, dict):
        for group in links.values():
            if isinstance(group, dict):
                href = group.get("href")
                if isinstance(href, str) and href.startswith("http"):
                    return href
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, dict) and str(item.get("href", "")).startswith("http"):
                        return item["href"]
    return None


def _article_text(article: dict[str, Any]) -> tuple[str, str, str]:
    title = str(article.get("headline") or article.get("title") or "").strip()
    description = str(
        article.get("description") or article.get("summary") or article.get("story") or ""
    ).strip()
    return title, description, f"{title}. {description}".strip()


def _published(article: dict[str, Any]) -> str | None:
    for key in ("published", "publishedAt", "lastModified", "date"):
        value = article.get(key)
        if value:
            return str(value)
    return None


def _season_from_article(article: dict[str, Any], fallback: int) -> int:
    published = _published(article)
    if published:
        match = re.match(r"(20\d{2})", published)
        if match:
            return int(match.group(1))
    return int(fallback)


def _body_part(text: str) -> str | None:
    lowered = text.casefold()
    for part in BODY_PARTS:
        if part.casefold() in lowered:
            return part
    return None


def _label(text: str, body_part: str | None) -> str:
    if "concussion" in text.casefold():
        return "Concussion"
    if body_part:
        return f"{body_part} injury"
    if re.search(r"\bsurger(?:y|ies|ical)\b", text, re.I):
        return "Surgery / injury"
    return "Injury"


def parse_injury_articles(payload: Any, *, fallback_season: int) -> list[dict[str, Any]]:
    """Extract conservative injury evidence from an ESPN athlete-news payload."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in _walk(payload):
        title, description, text = _article_text(article)
        if not title or not INJURY_TERMS.search(text):
            continue
        url = _article_url(article)
        if not url or url in seen:
            continue
        seen.add(url)
        part = _body_part(text)
        rows.append({
            "season": _season_from_article(article, fallback_season),
            "injury_label": _label(text, part),
            "body_part": part,
            "details": description or title,
            "season_ending": bool(SEASON_ENDING.search(text)),
            "source_name": "ESPN",
            "source_url": url,
            "source_published_at": _published(article),
            # Athlete-specific ESPN reporting explicitly describing an injury is
            # evidence, but we reserve "confirmed" for wording that states a
            # diagnosis, surgery, tear/fracture, or season-ending outcome.
            "confidence": "confirmed" if (
                part or SEASON_ENDING.search(text) or
                re.search(r"\b(torn|tear|fractur(?:e|ed)|surgery|concussion)\b", text, re.I)
            ) else "reported",
        })
    return rows


def sync_player(repository, player: dict[str, Any], season: int) -> dict[str, Any]:
    """Resolve one current player, scan ESPN athlete news, and upsert events."""
    initialize(repository)
    stints = player.get("stints") or []
    if len({int(row.get("season")) for row in stints if row.get("season") is not None}) < 2:
        return {"player_id": player.get("player_id"), "skipped": "new_recruit", "stored": 0}

    athlete_id = resolve_espn_athlete(player.get("name") or "")
    if not athlete_id:
        return {"player_id": player.get("player_id"), "skipped": "espn_unresolved", "stored": 0}
    try:
        payload = _json(NEWS_URL.format(athlete_id=athlete_id))
    except Exception as exc:
        return {"player_id": player.get("player_id"), "skipped": f"espn_news_error:{type(exc).__name__}", "stored": 0}

    rows = parse_injury_articles(payload, fallback_season=season)
    with closing(repository._connect()) as connection:
        for row in rows:
            connection.execute(
                """INSERT INTO player_injury_events(
                       player_id,season,espn_athlete_id,injury_label,body_part,details,
                       season_ending,source_name,source_url,source_published_at,confidence,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(player_id,source_url) DO UPDATE SET
                       season=excluded.season,espn_athlete_id=excluded.espn_athlete_id,
                       injury_label=excluded.injury_label,body_part=excluded.body_part,
                       details=excluded.details,season_ending=excluded.season_ending,
                       source_published_at=excluded.source_published_at,
                       confidence=excluded.confidence,updated_at=excluded.updated_at""",
                (
                    str(player["player_id"]), int(row["season"]), athlete_id,
                    row["injury_label"], row["body_part"], row["details"],
                    int(row["season_ending"]), row["source_name"], row["source_url"],
                    row["source_published_at"], row["confidence"], _now_iso(),
                ),
            )
        connection.commit()
    return {"player_id": player.get("player_id"), "espn_athlete_id": athlete_id, "stored": len(rows)}


def events_for_player(repository, player_id: str, *, through_season: int | None = None) -> list[dict[str, Any]]:
    initialize(repository)
    sql = """SELECT player_id,season,espn_athlete_id,injury_label,body_part,details,
                    season_ending,source_name,source_url,source_published_at,confidence
             FROM player_injury_events WHERE player_id=?"""
    params: list[Any] = [str(player_id)]
    if through_season is not None:
        sql += " AND season<=?"
        params.append(int(through_season))
    sql += " ORDER BY season ASC, source_published_at ASC"
    with closing(repository._connect()) as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def events_by_season(repository, player_id: str, *, through_season: int | None = None) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in events_for_player(repository, player_id, through_season=through_season):
        grouped.setdefault(int(row["season"]), []).append(row)
    return grouped
