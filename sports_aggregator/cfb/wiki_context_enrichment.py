"""Broaden slow-changing program-history coverage from Wikipedia.

The base Wikipedia integration prefers structured infobox fields. Program pages
vary enough that some omit or reshape those parameters even though the rendered
infobox exposes the facts consistently. This enrichment layer adds a rendered-
infobox fallback, keeps claimed and unclaimed national titles distinct, and
provides a compact conference-title label for the team hero.
"""

from __future__ import annotations

import re
from typing import Any

import requests
from bs4 import BeautifulSoup

PARSER_VERSION = 2


def _plain_text(wikitext: str) -> str:
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


def _clean_cell_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\[[^\]]*\]", "", value)
    text = re.sub(r"\s+", " ", text).strip(" ;,")
    return text or None


def _rendered_infobox_fields(html_text: str) -> dict[str, str]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    box = soup.select_one("table.infobox")
    if box is None:
        return {}
    fields: dict[str, str] = {}
    for row in box.select("tr"):
        header = row.find("th")
        cell = row.find("td")
        if header is None or cell is None:
            continue
        label = re.sub(r"[^a-z0-9]+", " ", header.get_text(" ", strip=True).casefold()).strip()
        value = _clean_cell_text(cell.get_text(" ", strip=True))
        if value:
            fields[label] = value
    return fields


def _rendered_history_fields(fields: dict[str, str]) -> dict[str, Any]:
    first_value = fields.get("first season") or fields.get("first year")
    first_year = None
    if first_value:
        match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", first_value)
        first_year = int(match.group(1)) if match else None

    claimed = fields.get("claimed national titles") or fields.get("claimed national championships")
    has_unclaimed = any("unclaimed national" in key for key in fields)
    if claimed is None and not has_unclaimed:
        claimed = fields.get("national titles") or fields.get("national championships")

    return {
        "first_season": first_year,
        "national_championships": claimed,
        "conference_championships": (
            fields.get("conference titles") or fields.get("conference championships")
        ),
        "mascot": fields.get("mascot"),
        "unclaimed_national_championships": (
            fields.get("unclaimed national titles") or
            fields.get("unclaimed national championships")
        ),
        "has_unclaimed_national_row": has_unclaimed,
    }


def _compact_conference_titles(value: Any) -> str | None:
    """Render a long conference-title list as ``count (most recent)``."""
    text = str(value or "").strip()
    if not text:
        return None
    years = [int(year) for year in re.findall(r"\b(?:18|19|20)\d{2}\b", text)]
    if years:
        unique_years = sorted(set(years))
        return f"{len(unique_years)} ({unique_years[-1]})"
    count = re.search(r"\b(\d{1,3})\b", text)
    return count.group(1) if count else text


def install_wiki_context_enrichment() -> None:
    from sports_aggregator.cfb import conference_extras, wiki_context

    current_fetch = wiki_context._fetch_wikipedia
    current_team_context = wiki_context.team_wiki_context
    if getattr(current_fetch, "_program_history_enriched_v2", False):
        return

    def enriched_fetch(school: str, mascot: str | None) -> dict[str, Any]:
        payload = current_fetch(school, mascot)
        page_title = payload.get("page_title")
        if page_title:
            session = requests.Session()
            session.headers.update({"User-Agent": wiki_context.USER_AGENT})
            rendered = session.get(wiki_context.WIKI_API, params={
                "action": "parse", "page": page_title,
                "prop": "text", "format": "json", "formatversion": 2,
            }, timeout=4)
            rendered.raise_for_status()
            rendered_html = rendered.json().get("parse", {}).get("text", "") or ""
            rendered_fields = _rendered_history_fields(_rendered_infobox_fields(rendered_html))

            for key in ("first_season", "conference_championships", "mascot"):
                if payload.get(key) in (None, "") and rendered_fields.get(key) not in (None, ""):
                    payload[key] = rendered_fields[key]

            if rendered_fields.get("national_championships") not in (None, ""):
                payload["national_championships"] = rendered_fields["national_championships"]
            elif rendered_fields.get("has_unclaimed_national_row"):
                # If Wikipedia explicitly distinguishes an unclaimed-title row
                # and provides no claimed-title row, the headline total is zero.
                # Do not preserve a generic value from the older parser.
                payload["national_championships"] = None

            if rendered_fields.get("unclaimed_national_championships") not in (None, ""):
                payload["unclaimed_national_championships"] = rendered_fields[
                    "unclaimed_national_championships"
                ]

            missing = [
                key for key in ("first_season", "conference_championships")
                if payload.get(key) in (None, "")
            ]
            # National-title prose is used only when the rendered infobox does
            # not explicitly distinguish unclaimed titles.
            if (payload.get("national_championships") in (None, "") and
                    not rendered_fields.get("has_unclaimed_national_row")):
                missing.append("national_championships")

            if missing:
                response = session.get(wiki_context.WIKI_API, params={
                    "action": "parse", "page": page_title,
                    "prop": "wikitext", "format": "json", "formatversion": 2,
                }, timeout=4)
                response.raise_for_status()
                wikitext = response.json().get("parse", {}).get("wikitext", "") or ""
                fallback = _fallback_fields(wikitext)
                for key in missing:
                    if fallback.get(key) not in (None, ""):
                        payload[key] = fallback[key]

        payload["wiki_parser_version"] = PARSER_VERSION
        return payload

    enriched_fetch._program_history_enriched_v2 = True
    wiki_context._fetch_wikipedia = enriched_fetch

    def enriched_team_context(repository, team: dict[str, Any], *, refresh: bool = False):
        result = current_team_context(repository, team, refresh=refresh)
        stored_payload = result.get("payload") or {}
        parser_version = stored_payload.get("wiki_parser_version")
        if not refresh and result.get("fetch_ok") and parser_version != PARSER_VERSION:
            result = current_team_context(repository, team, refresh=True)

        result["conference_championships_compact"] = _compact_conference_titles(
            result.get("conference_championships")
        )
        payload = result.get("payload") or {}
        result["unclaimed_national_championships"] = payload.get(
            "unclaimed_national_championships"
        )
        return result

    wiki_context.team_wiki_context = enriched_team_context
    conference_extras.team_wiki_context = enriched_team_context
