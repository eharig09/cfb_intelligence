"""Sourced historical injury annotations for CFB player career paths.

ESPN does not expose a reliable NCAAF injury-history collection comparable to
its NFL feed. This module therefore uses conservative ESPN evidence paths and
only attributes an injury when the named player and the injury language occur
in the same local clause. Absence from a game is never classified as an injury
by itself.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from sports_aggregator.cfb.repository import schema_once


SEARCH_URLS = (
    "https://site.web.api.espn.com/apis/search/v2?query={query}&sport=football&limit=20",
    "https://site.web.api.espn.com/apis/common/v3/search?query={query}&limit=20",
)
NEWS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/"
    "college-football/athletes/{athlete_id}/news?limit=50"
)
USER_AGENT = "cfb-intelligence/1.2 injury-research"

INJURY_TERMS = re.compile(
    r"\b(injur(?:y|ed|ies)|hurt|surgery|surgical|torn|tear|sprain(?:ed)?|strain(?:ed)?|"
    r"fractur(?:e|ed)|break|broken|concussion|acl|mcl|meniscus|achilles|hamstring|ankle|knee|"
    r"shoulder|foot|wrist|hand|elbow|back|hip|groin|leg|arm|neck|head)\b",
    re.I,
)
BODY_PARTS = (
    "ACL", "MCL", "meniscus", "Achilles", "concussion", "hamstring", "ankle", "knee",
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


@schema_once("player_injuries")
def initialize(repository) -> None:
    with closing(repository._connect()) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=15) as response:  # nosec - fixed ESPN hosts only
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
    """Legacy exact-name resolver retained for callers that do not have a CFBD ID."""
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
            raw_id = item.get("athleteId") or item.get("uid")
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


def _name_pattern(player_name: str) -> re.Pattern[str] | None:
    tokens = [re.escape(token) for token in re.findall(r"[A-Za-z0-9]+", player_name)]
    if not tokens:
        return None
    return re.compile(r"\b" + r"\W+".join(tokens) + r"\b", re.I)


def _attributed_injury_clause(text: str, player_name: str) -> str | None:
    """Return local evidence only when the named player owns the injury clause.

    A whole-summary match is too loose. For example, ``Giovanni El-Hadi ... with
    an injury, but ... Rod Moore ...`` mentions Rod and an injury in one sentence
    while clearly assigning the injury to El-Hadi. Split contrastive clauses and
    require the player's exact name and injury language inside the same clause.
    """
    name = _name_pattern(player_name)
    if name is None:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        clauses = re.split(
            r"\s*(?:;|—|–|,?\s+\b(?:but|while|whereas|although|however)\b\s+)\s*",
            sentence,
            flags=re.I,
        )
        for clause in clauses:
            if name.search(clause) and INJURY_TERMS.search(clause):
                return clause.strip()
    return None


def parse_injury_articles(
    payload: Any, *, fallback_season: int, required_player_name: str | None = None
) -> list[dict[str, Any]]:
    """Extract conservative, player-attributed injury evidence from ESPN payloads."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in _walk(payload):
        title, description, text = _article_text(article)
        if not title or not INJURY_TERMS.search(text):
            continue
        evidence = text
        if required_player_name:
            evidence = _attributed_injury_clause(text, required_player_name) or ""
            if not evidence:
                continue
        url = _article_url(article)
        if not url or url in seen:
            continue
        if "espn.com" not in url.casefold():
            continue
        seen.add(url)
        part = _body_part(evidence)
        rows.append({
            "season": _season_from_article(article, fallback_season),
            "injury_label": _label(evidence, part),
            "body_part": part,
            "details": description or title,
            "season_ending": bool(SEASON_ENDING.search(evidence)),
            "source_name": "ESPN",
            "source_url": url,
            "source_published_at": _published(article),
            "confidence": "confirmed" if (
                part or SEASON_ENDING.search(evidence) or
                re.search(r"\b(torn|tear|fractur(?:e|ed)|surgery|concussion)\b", evidence, re.I)
            ) else "reported",
        })
    return rows


def _search_injury_articles(player_name: str, season: int) -> list[dict[str, Any]]:
    """Fallback to ESPN's search index while preserving strict attribution."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for suffix in ("injury", "injured", "surgery"):
        query = quote_plus(f'"{player_name}" {suffix}')
        for template in SEARCH_URLS:
            try:
                payload = _json(template.format(query=query))
            except Exception:
                continue
            for row in parse_injury_articles(
                payload, fallback_season=season, required_player_name=player_name
            ):
                if row["source_url"] not in seen:
                    seen.add(row["source_url"])
                    rows.append(row)
            break
    return rows


def _store_rows(repository, player_id: str, athlete_id: str | None,
                rows: list[dict[str, Any]]) -> int:
    """Replace this player's ESPN-derived injury evidence on every sync.

    The ESPN layer is reproducible derived data. Replacing rather than merely
    upserting ensures parser improvements remove stale false positives.
    """
    with closing(repository._connect()) as connection:
        connection.execute(
            "DELETE FROM player_injury_events WHERE player_id=? AND source_name='ESPN'",
            (str(player_id),),
        )
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
                    str(player_id), int(row["season"]), athlete_id,
                    row["injury_label"], row["body_part"], row["details"],
                    int(row["season_ending"]), row["source_name"], row["source_url"],
                    row["source_published_at"], row["confidence"], _now_iso(),
                ),
            )
        connection.commit()
    return len(rows)


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
