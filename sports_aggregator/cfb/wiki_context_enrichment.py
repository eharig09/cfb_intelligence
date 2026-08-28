"""Broaden slow-changing program-history coverage from Wikipedia.

The base Wikipedia integration prefers structured infobox fields. Some program
articles omit one or more of those parameters even though the article text
states the same fact plainly. This module adds conservative text fallbacks and
forces a retry when a cached row has none of the three headline history fields.
"""

from __future__ import annotations

import re
from typing import Any

import requests


def _plain_text(wikitext: str) -> str:
    """Reduce wikitext enough for conservative sentence-level regex fallbacks."""
    text = re.sub(r"<!--.*?-->", " ", wikitext, flags=re.S)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", " ", text, flags=re.S | re.I)
    text = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_year_from_text(text: str) -> int | None:
    patterns = (
        r"(?:first\s+(?:fielded|played|began)|began\s+(?:playing|play|competition)|started\s+(?:playing|play))[^.]{0,90}?\b(18\d{2}|19\d{2}|20\d{2})\b",
        r"football\s+program[^.]{0,90}?(?:began|started|dates\s+to|was\s+established\s+in)[^.]{0,40}?\b(18\d{2}|19\d{2}|20\d{2})\b",
        r"first\s+season[^.]{0,40}?\b(18\d{2}|19\d{2}|20\d{2})\b",
    )
    lowered = text.casefold()
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def _championship_count(text: str, kind: str) -> str | None:
    if kind == "national":
        patterns = (
            r"\bclaims?\s+(\d+)\s+(?:recognized\s+)?national\s+championships?\b",
            r"\b(?:has|have)\s+won\s+(\d+)\s+national\s+championships?\b",
            r"\brecognizes?\s+(\d+)\s+national\s+championships?\b",
        )
    else:
        patterns = (
            r"\b(?:has|have)\s+won\s+(\d+)\s+conference\s+championships?\b",
            r"\bclaims?\s+(\d+)\s+conference\s+championships?\b",
            r"\b(?:has|have)\s+earned\s+(\d+)\s+conference\s+championships?\b",
        )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _fallback_fields(wikitext: str) -> dict[str, Any]:
    text = _plain_text(wikitext)
    return {
        "first_season": _first_year_from_text(text),
        "national_championships": _championship_count(text, "national"),
        "conference_championships": _championship_count(text, "conference"),
    }


def install_wiki_context_enrichment() -> None:
    """Patch the existing integration without changing its storage contract."""
    from sports_aggregator.cfb import conference_extras, wiki_context

    current_fetch = wiki_context._fetch_wikipedia
    current_team_context = wiki_context.team_wiki_context
    if getattr(current_fetch, "_program_history_enriched", False):
        return

    def enriched_fetch(school: str, mascot: str | None) -> dict[str, Any]:
        payload = current_fetch(school, mascot)
        missing = [
            key for key in ("first_season", "national_championships", "conference_championships")
            if payload.get(key) in (None, "")
        ]
        if not missing or not payload.get("page_title"):
            return payload

        session = requests.Session()
        session.headers.update({"User-Agent": wiki_context.USER_AGENT})
        response = session.get(wiki_context.WIKI_API, params={
            "action": "parse", "page": payload["page_title"],
            "prop": "wikitext", "format": "json", "formatversion": 2,
        }, timeout=4)
        response.raise_for_status()
        wikitext = response.json().get("parse", {}).get("wikitext", "") or ""
        fallback = _fallback_fields(wikitext)
        for key in missing:
            if fallback.get(key) not in (None, ""):
                payload[key] = fallback[key]
        payload["fallback_fields"] = {
            key: payload.get(key) for key in missing if fallback.get(key) not in (None, "")
        }
        return payload

    enriched_fetch._program_history_enriched = True
    wiki_context._fetch_wikipedia = enriched_fetch

    def enriched_team_context(repository, team: dict[str, Any], *, refresh: bool = False):
        result = current_team_context(repository, team, refresh=refresh)
        headline = (
            result.get("first_season"),
            result.get("national_championships"),
            result.get("conference_championships"),
        )
        # A successful page lookup with none of the three headline history facts
        # is almost always a parser miss, not a useful cache entry. Retry once
        # through the enriched fetch path instead of preserving it for 90 days.
        if not refresh and result.get("fetch_ok") and all(value in (None, "") for value in headline):
            result = current_team_context(repository, team, refresh=True)
        return result

    wiki_context.team_wiki_context = enriched_team_context
    # conference_extras imported the function directly, so replace that bound
    # reference too; team_schedule_elo resolves this module global at call time.
    conference_extras.team_wiki_context = enriched_team_context
