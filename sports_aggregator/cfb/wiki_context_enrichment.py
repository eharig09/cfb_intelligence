"""Broaden slow-changing program-history coverage from Wikipedia.

Wikipedia program pages use several infobox/template variants. The base
integration prefers raw infobox parameters; this layer falls back to the
rendered infobox labels, understands nested championship sections, keeps
claimed and unclaimed national titles separate, and normalizes conference-title
display to ``count (most recent)``.
"""

from __future__ import annotations

import re
from typing import Any

import requests
from bs4 import BeautifulSoup

PARSER_VERSION = 4


def _plain_text(wikitext: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", wikitext, flags=re.S)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", " ", text, flags=re.S | re.I)
    text = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_year_from_text(text: str) -> int | None:
    for pattern in (
        r"(?:first\s+(?:fielded|played|began)|began\s+(?:playing|play|competition)|started\s+(?:playing|play))[^.]{0,90}?\b(18\d{2}|19\d{2}|20\d{2})\b",
        r"football\s+program[^.]{0,90}?(?:began|started|dates\s+to|was\s+established\s+in)[^.]{0,40}?\b(18\d{2}|19\d{2}|20\d{2})\b",
        r"first\s+(?:season|year)[^.]{0,40}?\b(18\d{2}|19\d{2}|20\d{2})\b",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def _championship_count(text: str, kind: str) -> str | None:
    noun = "national" if kind == "national" else "conference"
    for pattern in (
        rf"\bclaims?\s+(\d+)\s+(?:recognized\s+)?{noun}\s+championships?\b",
        rf"\b(?:has|have)\s+won\s+(\d+)\s+(?:officially\s+recognized\s+)?(?:[A-Z0-9-]+\s+)?{noun}\s+championships?\b",
        rf"\b(?:has|have)\s+earned\s+(\d+)\s+{noun}\s+championships?\b",
    ):
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


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\[[^\]]*\]", "", value)
    text = re.sub(r"\s+", " ", text).strip(" ;,")
    return text or None


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _rendered_infobox(html_text: str) -> dict[str, str]:
    """Flatten the rendered infobox while retaining championship section context."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    box = soup.select_one("table.infobox")
    if box is None:
        return {}
    result: dict[str, str] = {}
    section: str | None = None
    conference_values: list[str] = []

    # MediaWiki commonly wraps table rows in tbody. Walk all rows whose closest
    # table ancestor is the program infobox, excluding rows from nested tables.
    for row in box.find_all("tr"):
        if row.find_parent("table") is not box:
            continue
        header, cell = row.find("th", recursive=False), row.find("td", recursive=False)
        if header is None:
            continue

        header_text = _label(header.get_text(" ", strip=True))
        if cell is None:
            section = header_text or None
            continue

        value = _clean_text(cell.get_text(" ", strip=True))
        if not header_text or not value:
            continue

        result[header_text] = value
        if section in {"national championships", "national titles"}:
            if header_text == "claimed":
                result["claimed national titles"] = value
            elif header_text == "unclaimed":
                result["unclaimed national titles"] = value
        elif section in {"conference championships", "conference titles"}:
            conference_values.append(value)

    if conference_values:
        result["conference championships"] = " ; ".join(conference_values)
    return result


def _rendered_history(fields: dict[str, str]) -> dict[str, Any]:
    first = fields.get("first season") or fields.get("first year")
    first_year = None
    if first:
        match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", first)
        first_year = int(match.group(1)) if match else None

    claimed = fields.get("claimed national titles") or fields.get("claimed national championships")
    has_unclaimed = bool(
        fields.get("unclaimed national titles") or fields.get("unclaimed national championships")
    )
    if claimed is None and not has_unclaimed:
        claimed = fields.get("national titles") or fields.get("national championships")

    return {
        "first_season": first_year,
        "conference_championships": fields.get("conference titles") or fields.get("conference championships"),
        "national_championships": claimed,
        "mascot": fields.get("mascot"),
        "has_unclaimed": has_unclaimed,
    }


def _compact_titles(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    years = sorted(set(int(year) for year in re.findall(r"\b(?:18|19|20)\d{2}\b", text)))
    if years:
        return f"{len(years)} ({years[-1]})"
    count = re.search(r"\b(\d{1,3})\b", text)
    return count.group(1) if count else text


def install_wiki_context_enrichment() -> None:
    from sports_aggregator.cfb import conference_extras, wiki_context

    base_fetch = wiki_context._fetch_wikipedia
    base_context = wiki_context.team_wiki_context
    if getattr(base_fetch, "_program_history_v4", False):
        return

    def enriched_fetch(school: str, mascot: str | None) -> dict[str, Any]:
        payload = base_fetch(school, mascot)
        page_title = payload.get("page_title")
        rendered_fields: dict[str, Any] = {}

        if page_title:
            session = requests.Session()
            session.headers.update({"User-Agent": wiki_context.USER_AGENT})
            rendered = session.get(wiki_context.WIKI_API, params={
                "action": "parse", "page": page_title, "prop": "text",
                "format": "json", "formatversion": 2,
            }, timeout=4)
            rendered.raise_for_status()
            rendered_fields = _rendered_history(
                _rendered_infobox(rendered.json().get("parse", {}).get("text", "") or "")
            )

            for key in ("first_season", "conference_championships", "mascot"):
                if rendered_fields.get(key) not in (None, ""):
                    payload[key] = rendered_fields[key]

            if rendered_fields.get("national_championships") not in (None, ""):
                payload["national_championships"] = rendered_fields["national_championships"]
            elif rendered_fields.get("has_unclaimed"):
                payload["national_championships"] = None

            missing = [key for key in ("first_season", "conference_championships")
                       if payload.get(key) in (None, "")]
            if (payload.get("national_championships") in (None, "") and
                    not rendered_fields.get("has_unclaimed")):
                missing.append("national_championships")

            if missing:
                raw = session.get(wiki_context.WIKI_API, params={
                    "action": "parse", "page": page_title, "prop": "wikitext",
                    "format": "json", "formatversion": 2,
                }, timeout=4)
                raw.raise_for_status()
                wikitext = raw.json().get("parse", {}).get("wikitext", "") or ""
                fallback = _fallback_fields(wikitext)
                for key in missing:
                    if fallback.get(key) not in (None, ""):
                        payload[key] = fallback[key]

            summary = str(payload.get("summary") or "")
            if payload.get("conference_championships") in (None, ""):
                payload["conference_championships"] = _championship_count(summary, "conference")
            if (payload.get("national_championships") in (None, "") and
                    not rendered_fields.get("has_unclaimed")):
                payload["national_championships"] = _championship_count(summary, "national")

        payload["conference_championships_full"] = payload.get("conference_championships")
        payload["wiki_parser_version"] = PARSER_VERSION
        return payload

    enriched_fetch._program_history_v4 = True
    wiki_context._fetch_wikipedia = enriched_fetch

    def enriched_context(repository, team: dict[str, Any], *, refresh: bool = False):
        result = base_context(repository, team, refresh=refresh)
        stored = result.get("payload") or {}
        if (not refresh and result.get("fetch_ok") and
                stored.get("wiki_parser_version") != PARSER_VERSION):
            result = base_context(repository, team, refresh=True)
            stored = result.get("payload") or {}

        full_conf = stored.get("conference_championships_full") or result.get("conference_championships")
        result["conference_championships_full"] = full_conf
        result["conference_championships"] = _compact_titles(full_conf)
        return result

    wiki_context.team_wiki_context = enriched_context
    conference_extras.team_wiki_context = enriched_context
