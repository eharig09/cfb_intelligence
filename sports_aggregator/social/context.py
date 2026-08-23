"""Tests for whether an item is about college football or the professional game.

An unscoped player match -- a name unique across the roster whose team was never
named in the text -- is the widest rule the resolver has, and it is where false
positives come from. Names are shared across levels: the Chiefs' Trey Smith is
not Purdue's, and a Jaguars preseason post is not about a Vanderbilt lineman.

These patterns gate that rule. They are deliberately blunt: a pro marker anywhere
in the item blocks the unscoped match entirely, because a wrong player link is
worse than a missing one.
"""

from __future__ import annotations

import re


#: Professional-football markers. Presence of any blocks an unscoped match.
PRO_TERMS = (
    "nfl", "nfc", "afc", "chiefs", "jaguars", "jags", "bengals", "packers",
    "steelers", "ravens", "browns", "bills", "dolphins", "patriots", "jets",
    "texans", "colts", "titans", "broncos", "raiders", "chargers", "cowboys",
    "eagles", "commanders", "giants", "bears", "lions", "vikings", "falcons",
    "panthers", "saints", "buccaneers", "bucs", "cardinals", "rams", "niners",
    "49ers", "seahawks", "pro bowl", "super bowl", "training camp battle",
    "free agency", "practice squad", "active roster",
)

#: Evidence the item really is college football.
COLLEGE_TERMS = (
    "college football", "cfb", "ncaa", "fbs", "heisman", "transfer portal",
    "big ten", "sec", "acc", "big 12", "pac-12", "mountain west", "sun belt",
    "conference usa", "american athletic", "bowl game", "playoff", "signing day",
    "spring game", "coordinator", "walk-on", "redshirt", "true freshman",
    "recruit", "recruits", "recruiting", "commit", "commits", "commitment",
)

#: Coaching titles. A name next to one of these is a staff member, and staff
#: names collide with player names often enough to matter.
STAFF_TERMS = (
    "head coach", "offensive coordinator", "defensive coordinator",
    "coordinator", "position coach", "coaching staff", "assistant coach",
)


def _boundary_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Word-boundary alternation built in code, not written as an escape.

    Assembling the boundary here keeps the pattern correct regardless of how the
    source file was edited; a hand-written escape was silently written as a
    literal control character once, and the guard matched nothing at all.
    """
    boundary = chr(92) + "b"
    alternation = "|".join(re.escape(term) for term in terms)
    return re.compile(f"{boundary}(?:{alternation}){boundary}", re.I)


PRO_CONTEXT = _boundary_pattern(PRO_TERMS)
COLLEGE_CONTEXT = _boundary_pattern(COLLEGE_TERMS)
STAFF_CONTEXT = _boundary_pattern(STAFF_TERMS)


def is_pro_context(text: str) -> bool:
    return bool(PRO_CONTEXT.search(text))


def is_college_context(text: str) -> bool:
    return bool(COLLEGE_CONTEXT.search(text))


def names_staff(text: str, name: str) -> bool:
    """Whether a coaching title sits within a few words of this name.

    "SEC previews: LSU coordinator Blake Baker" should not resolve to a Louisiana
    Tech player who happens to share the name.
    """
    for match in re.finditer(re.escape(name), text, re.I):
        window = text[max(0, match.start() - 60): match.end() + 60]
        if STAFF_CONTEXT.search(window):
            return True
    return False


def allows_unscoped_match(text: str, *, has_resolved_team: bool) -> bool:
    """Whether an unscoped player match may be made at all for this item."""
    window = text[:2000]
    if is_pro_context(window):
        return False
    return has_resolved_team or is_college_context(window)
