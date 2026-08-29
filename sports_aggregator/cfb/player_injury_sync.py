"""Reliable ESPN injury sync built on top of the conservative injury parser.

CFBD player IDs are commonly the same athlete IDs ESPN uses. Prefer that stable
identity over extracting a generic numeric ``id`` from ESPN search payloads,
which can describe a result/container rather than the athlete itself.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote_plus

from sports_aggregator.cfb.player_injuries import (
    NEWS_URL,
    SEARCH_URLS,
    _json,
    _search_injury_articles,
    _store_rows,
    _walk,
    initialize,
    parse_injury_articles,
)


def _positive_numeric_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.isdigit() and int(text) > 0 else None


def _athlete_id_from_item(item: dict[str, Any]) -> str | None:
    """Extract only IDs that are explicitly identified as athlete identities."""
    raw = item.get("athleteId")
    direct = _positive_numeric_id(raw)
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

    # A plain ``id`` is accepted only when the object itself is clearly an
    # athlete/player object. This avoids container/result IDs such as ``0213``.
    kind = " ".join(str(item.get(key) or "") for key in ("type", "subtype", "entityType")).casefold()
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
            rows.extend(parse_injury_articles(payload, fallback_season=season))
            news_status = "ok"
        except HTTPError as exc:
            news_status = f"http_{exc.code}"
        except Exception as exc:
            news_status = type(exc).__name__

    fallback_rows = _search_injury_articles(player_name, season)
    seen = {row["source_url"] for row in rows}
    rows.extend(row for row in fallback_rows if row["source_url"] not in seen)

    stored = _store_rows(repository, str(player["player_id"]), athlete_id, rows)
    return {
        "player_id": player.get("player_id"),
        "espn_athlete_id": athlete_id,
        "stored": stored,
        "athlete_news_status": news_status,
        "search_matches": len(fallback_rows),
        "skipped": None if rows else "no_injury_evidence",
    }
