"""Determine what an item *is* — reporting, corroboration, analysis — and say why.

`REPORTING_UNDETERMINED` was assigned to everything a reporter posted, because
the source class was the only evidence available at ingestion. It is a placeholder
that says "a journalist wrote this", not a finding, and it filled the role column
with a word that told the reader nothing.

Determination now reads the text itself. Journalists mark their own work: original
reporting announces that it *learned* something, corroboration credits whoever had
it first, and commentary argues rather than reports. Those markers are conventional
enough to classify on, and weak enough that every verdict carries its evidence and
falls back to plain `REPORTING` rather than guessing.

Nothing here promotes an item to fact. The role decides how an item is labeled and
weighted, never whether its claim is true.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence


#: First-hand reporting: the outlet is the origin of the information.
ORIGINAL_MARKERS = (
    (r"\bsources?\s+(?:tell|told|say|said|confirm)", "attributes to its own sources"),
    (r"\bhas\s+learned\b", "states it learned the information"),
    (r"\bi'?m\s+told\b", "first-hand attribution"),
    (r"\b(?:can\s+)?confirm(?:ed|s)?\s+(?:that|to)\b", "states it confirmed the information"),
    (r"\bexclusive\b", "labeled exclusive"),
    (r"\bbreaking\b", "labeled breaking"),
    (r"\baccording to (?:a )?(?:source|people|those)", "attributes to unnamed sources"),
)

#: Credit to someone else: the item is passing along another outlet's work.
CORROBORATION_MARKERS = (
    (r"\b(?:per|via)\s+@?\w", "credits another outlet"),
    (r"\baccording to\s+(?:the\s+)?[A-Z]", "cites a named outlet"),
    (r"\b(?:first\s+reported|reported\s+first)\b", "credits the first report"),
    (r"\breports?\s+@\w", "credits a named reporter"),
    (r"\bh/?t\s+@?\w", "hat tip to another account"),
)

#: Argument rather than new information.
COMMENTARY_MARKERS = (
    (r"\b(?:i think|my take|in my (?:view|opinion)|hot take)\b", "states a personal view"),
    (r"\b(?:should|shouldn'?t|needs? to)\b.{0,40}\b(?:fire|hire|start|bench)\b", "argues for an action"),
    (r"\bwhy\s+\w+\s+(?:will|should|won'?t|shouldn'?t)\b", "argues a position"),
    (r"\bpredictions?\b|\b(?:my|our|week\s+\d+)\s+picks\b|\bbest bets\b",
     "prediction rather than report"),
)

#: Official channels speaking for the institution.
OFFICIAL_MARKERS = (
    (r"\bpress release\b|\bofficial statement\b", "official release"),
    (r"\bannounce(?:d|s|ment)\b", "announcement language"),
)

#: Content types that already state what the item is; text markers cannot override.
CONTENT_TYPE_ROLES = {
    "LINK_DISCOVERY": ("AGGREGATION", "links out to another publisher"),
    "GAME_THREAD": ("COMMUNITY_REACTION", "community game thread"),
    "POSTGAME_THREAD": ("COMMUNITY_REACTION", "community postgame thread"),
    "QUESTION": ("COMMUNITY_REACTION", "community question"),
    "RUMOR": ("COMMENTARY", "unverified rumor"),
    "PRESS_CONFERENCE": ("OFFICIAL_CONFIRMATION", "press conference"),
    "HIGHLIGHTS": ("AGGREGATION", "highlight package"),
    "FILM_BREAKDOWN": ("ANALYSIS", "film breakdown"),
    "DRAFT_ANALYSIS": ("ANALYSIS", "draft evaluation"),
    "SCOUTING_OPINION": ("ANALYSIS", "scouting opinion"),
    "STATISTICAL_ANALYSIS": ("ANALYSIS", "statistical analysis"),
}

#: Source classes that make a role plausible in the first place.
REPORTING_CLASSES = {"NATIONAL_REPORTER", "BEAT_REPORTER", "RECRUITING_REPORTER",
                     "PORTAL_REPORTER", "PUBLICATION", "LOCAL_OUTLET"}
ANALYST_CLASSES = {"NATIONAL_ANALYST", "TEAM_ANALYST", "FILM_ANALYST", "DRAFT_ANALYST",
                   "SCOUT", "MODEL", "DATA_PROVIDER", "PODCAST", "YOUTUBE_SHOW"}
OFFICIAL_CLASSES = {"PRIMARY_SOURCE", "OFFICIAL_TEAM", "OFFICIAL_CONFERENCE", "OFFICIAL_AWARD"}


def _match(text: str, markers: Iterable[tuple[str, str]]) -> str | None:
    for pattern, reason in markers:
        if re.search(pattern, text, re.I):
            return reason
    return None


def determine_role(*, text: str, content_type: str | None, classes: set[str],
                   platform: str, links_external: bool = False,
                   cluster_position: int | None = None,
                   cluster_size: int = 1) -> dict[str, Any]:
    """Classify one item and return the role with the evidence behind it.

    ``cluster_position`` is the item's rank by publication time inside its story
    cluster, so a later item from a credible outlet reads as corroboration even
    when its wording is neutral.
    """
    evidence: list[str] = []
    body = (text or "")[:1200]

    if classes & OFFICIAL_CLASSES:
        return {"role": "OFFICIAL_CONFIRMATION", "confidence": 0.9,
                "evidence": ["source is an official channel"]}
    if "BOT" in classes:
        return {"role": "AUTOMATED", "confidence": 0.95,
                "evidence": ["source is a registered automated account"]}

    typed = CONTENT_TYPE_ROLES.get(content_type or "")
    if typed:
        role, reason = typed
        return {"role": role, "confidence": 0.85, "evidence": [reason]}

    reporting_source = bool(classes & REPORTING_CLASSES)
    analyst_source = bool(classes & ANALYST_CLASSES)

    official_marker = _match(body, OFFICIAL_MARKERS)
    original_marker = _match(body, ORIGINAL_MARKERS)
    corroboration_marker = _match(body, CORROBORATION_MARKERS)
    commentary_marker = _match(body, COMMENTARY_MARKERS)

    # Crediting someone else outranks a first-hand marker: an item that says
    # "per @x, sources tell them" is relaying, not reporting.
    if corroboration_marker and reporting_source:
        evidence.append(corroboration_marker)
        if cluster_position and cluster_position > 1:
            evidence.append(f"item {cluster_position} of {cluster_size} in its story cluster")
        return {"role": "CORROBORATION", "confidence": 0.75, "evidence": evidence}

    if original_marker and reporting_source:
        evidence.append(original_marker)
        if cluster_position == 1 and cluster_size > 1:
            evidence.append("earliest item in its story cluster")
            return {"role": "ORIGINAL_REPORT", "confidence": 0.8, "evidence": evidence}
        return {"role": "ORIGINAL_REPORT", "confidence": 0.7, "evidence": evidence}

    if commentary_marker and not original_marker:
        evidence.append(commentary_marker)
        return {"role": "ANALYSIS" if analyst_source else "COMMENTARY",
                "confidence": 0.65, "evidence": evidence}

    if official_marker and reporting_source:
        return {"role": "REPORTING", "confidence": 0.6,
                "evidence": [official_marker, "relaying an announcement"]}

    if reporting_source:
        if cluster_position and cluster_position > 1:
            return {"role": "CORROBORATION", "confidence": 0.55,
                    "evidence": [f"item {cluster_position} of {cluster_size} in its story cluster"]}
        # Plain "REPORTING" is honest: a journalist published it, and nothing in
        # the text establishes whether the information originated with them.
        return {"role": "REPORTING", "confidence": 0.5,
                "evidence": ["published by a reporting source",
                             "no origin marker in the text"]}
    if analyst_source:
        return {"role": "ANALYSIS", "confidence": 0.6,
                "evidence": ["published by an analysis source"]}
    if links_external:
        return {"role": "AGGREGATION", "confidence": 0.6, "evidence": ["links to another publisher"]}
    if platform == "reddit":
        return {"role": "COMMUNITY_REACTION", "confidence": 0.6, "evidence": ["community platform"]}
    return {"role": "UNCLASSIFIED", "confidence": 0.3, "evidence": ["no role signal available"]}


#: Human labels. The stored role stays machine-readable; this is what a reader sees.
ROLE_LABELS = {
    "ORIGINAL_REPORT": "Original report",
    "CORROBORATION": "Corroborating",
    "REPORTING": "Reporting",
    "REPORTING_UNDETERMINED": "Reporting",
    "OFFICIAL_CONFIRMATION": "Official",
    "ANALYSIS": "Analysis",
    "COMMENTARY": "Opinion",
    "AGGREGATION": "Link-out",
    "COMMUNITY_REACTION": "Community",
    "AUTOMATED": "Automated",
    "UNCLASSIFIED": "Unclassified",
}


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", (role or "").replace("_", " ").title() or "Unclassified")
