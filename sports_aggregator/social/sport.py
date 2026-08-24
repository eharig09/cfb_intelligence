"""Explainable sport eligibility decisions for incoming CFB content.

The source of an item is useful context, but it is never sufficient evidence on
its own.  A Houston newspaper can publish about the Texas Longhorns, the Astros,
or city government; the words in the item must establish the sport before team,
player, and game candidates are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

from sports_aggregator.social.context import is_college_context, is_pro_context


CLASSIFIER_VERSION = "cfb-sport-v1"

OTHER_SPORTS = re.compile(
    r"\b(?:(?:men(?:'s)?|women(?:'s)?)\s+)?(?:basketball|baseball|softball|soccer|"
    r"volleyball|hockey|lacrosse|golf|tennis|wrestling|gymnastics|swimming|"
    r"diving|rowing)\b|\b(?:track and field|cross country|hoops|hardwood|diamond|"
    r"point guard|shooting guard|small forward|power forward|pitcher|catcher|"
    r"shortstop|home run|innings?|earned run|hat trick)\b",
    re.I,
)

FOOTBALL_EVIDENCE = re.compile(
    r"\b(?:college football|football|gridiron|quarterback|\bqb\b|running back|"
    r"wide receiver|tight end|offensive line|defensive line|offensive tackle|"
    r"defensive tackle|defensive end|linebacker|cornerback|nickelback|free safety|"
    r"strong safety|"
    r"touchdown|kickoff|field goal|punter|punt return|pass rush|depth chart|"
    r"spring practice|spring game|fall camp|bowl game|transfer portal|redshirt|"
    r"college football playoff|\bcfp\b|heisman)\w*\b",
    re.I,
)

# These phrases become meaningful when an actual college team is also named.
TEAM_FOOTBALL_CONTEXT = re.compile(
    r"\b(?:opener|practice|roster|starter|coordinator|recruit|commit|signing day|"
    r"injur|suspend|scrimmage|preseason|season preview|game preview|postgame|"
    r"week\s+(?:zero|\d+)|hosts?|travels? to|defeats?|beats?|falls? to)\w*\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class SportDecision:
    sport: str
    decision: str
    eligible: bool
    confidence: float
    method: str
    evidence: tuple[str, ...]


def classify_cfb_eligibility(
    text: str,
    *,
    title: str = "",
    team_candidates: Sequence[tuple[int, float, str]] = (),
    provider_team_ids: Iterable[int] = (),
    provider_player_ids: Iterable[str] = (),
    provider_game_ids: Iterable[int] = (),
    source_specialties: Iterable[str] = (),
) -> SportDecision:
    """Classify an item before any entity candidate becomes an accepted link.

    ``REVIEW`` decisions are retained in the content store but kept out of team,
    player, game, and story surfaces. This deliberately favours precision over
    silently attaching an uncertain item to the wrong program.
    """
    value = " ".join(str(text or "").split())
    headline = " ".join(str(title or "").split())
    scan = f"{headline} {value}".strip()
    evidence: list[str] = []
    other = sorted({match.group(0).casefold() for match in OTHER_SPORTS.finditer(scan)})
    football = sorted({match.group(0).casefold() for match in FOOTBALL_EVIDENCE.finditer(scan)})
    college = is_college_context(scan)
    professional = is_pro_context(scan)
    explicit_teams = [candidate for candidate in team_candidates
                      if candidate[1] >= 0.75 and candidate[2] != "source_team_scope"]
    provider_scope = bool(tuple(provider_team_ids) or tuple(provider_player_ids)
                          or tuple(provider_game_ids))
    specialties = {str(value).casefold().replace("-", "_")
                   for value in source_specialties}
    cfb_source = bool(specialties & {
        "college_football", "national_cfb", "team_community", "cfb",
    })

    if other:
        evidence.append("other_sport:" + ",".join(other[:3]))
    if football:
        evidence.append("football_terms:" + ",".join(football[:4]))
    if college:
        evidence.append("college_context")
    if explicit_teams:
        evidence.append(f"explicit_team_candidates:{len(explicit_teams)}")
    if provider_scope:
        evidence.append("provider_entity_scope")
    if cfb_source:
        evidence.append("cfb_source_specialty")

    # An explicit non-football sport wins unless the item also contains clear
    # football language. This handles multi-sport feeds without discarding a
    # football story that compares two athletic programs broadly.
    if other and not football:
        return SportDecision("OTHER", "REJECT", False, 0.98,
                             "explicit_other_sport", tuple(evidence))
    if professional and not college and not explicit_teams and not provider_scope:
        return SportDecision("NFL", "REJECT", False, 0.95,
                             "professional_football_context", tuple(evidence + ["pro_context"]))
    if college and football:
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.99,
                             "explicit_college_football", tuple(evidence))
    if football and (explicit_teams or provider_scope):
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.94,
                             "football_with_college_entity", tuple(evidence))
    if football and cfb_source:
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.91,
                             "football_with_cfb_source", tuple(evidence))
    if college and (explicit_teams or provider_scope):
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.92,
                             "college_context_with_entity", tuple(evidence))
    if explicit_teams and TEAM_FOOTBALL_CONTEXT.search(scan):
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.88,
                             "team_with_football_event", tuple(evidence + ["football_event_language"]))
    if explicit_teams and cfb_source:
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.86,
                             "team_with_cfb_source", tuple(evidence))
    if provider_scope and not other and (football or college or TEAM_FOOTBALL_CONTEXT.search(scan)):
        return SportDecision("COLLEGE_FOOTBALL", "ACCEPT", True, 0.86,
                             "provider_scope_with_context", tuple(evidence))
    return SportDecision("UNKNOWN", "REVIEW", False, 0.50,
                         "insufficient_sport_evidence", tuple(evidence or ["no_sport_signal"]))
