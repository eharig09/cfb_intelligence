"""Rank unit-versus-unit matchups by how interesting they are to watch.

A game page can already list every offense-versus-defense comparison it has
grades for. That is a table, not a reason to watch. This module scores each
comparison so the two or three that actually decide a game rise to the top, and
labels *why* each one is interesting rather than emitting a bare number.

Three things make a matchup worth watching:

* **Quality** - the better unit is genuinely good. A bad line against a bad
  front is lopsided and still not interesting.
* **Separation** - one side clearly outclasses the other, which is where a game
  gets decided.
* **Mutual strength** - both units are strong, which is the marquee case a
  separation term alone would rank last.

The scorer takes a neutral `MatchupSignal`, so compiled season statistics and
model outputs can feed the same ranking later without changing the callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


#: Grade floor below which a unit is not treated as a real strength. PFF college
#: grades cluster in the 60s, so 60 is "replaceable" and 80 is genuinely good.
GRADE_FLOOR = 60.0
GRADE_CEILING = 90.0

#: Minimum graded players and usage before a comparison is trusted at full value.
MIN_PLAYERS = 2
MIN_USAGE = 150.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class MatchupSignal:
    """One directional comparison, independent of the provider that produced it."""

    label: str
    attack_team: str
    attack_label: str
    attack_grade: float | None
    attack_players: int
    attack_usage: float
    defend_team: str
    defend_label: str
    defend_grade: float | None
    defend_players: int
    defend_usage: float
    provider: str = "PFF 2025"

    @property
    def complete(self) -> bool:
        return self.attack_grade is not None and self.defend_grade is not None

    @property
    def confident(self) -> bool:
        """Whether both sides carry enough graded snaps to be worth ranking."""
        return (self.attack_players >= MIN_PLAYERS and self.defend_players >= MIN_PLAYERS
                and self.attack_usage >= MIN_USAGE and self.defend_usage >= MIN_USAGE)


def score_matchup(signal: MatchupSignal) -> dict[str, Any] | None:
    """Score one comparison and explain the score in plain language."""
    if not signal.complete:
        return None
    attack, defend = float(signal.attack_grade), float(signal.defend_grade)
    span = GRADE_CEILING - GRADE_FLOOR
    stronger, weaker = max(attack, defend), min(attack, defend)
    difference = attack - defend

    quality = _clamp((stronger - GRADE_FLOOR) / span)
    separation = _clamp(abs(difference) / 15.0)
    mutual = _clamp((weaker - (GRADE_FLOOR + 5)) / span)

    interest = 100 * quality * _clamp(0.40 + 0.34 * separation + 0.34 * mutual)
    if not signal.confident:
        # Thin samples still appear, but must not outrank a well-sampled unit.
        interest *= 0.6

    if mutual >= 0.45 and separation < 0.35:
        archetype, headline = "STRENGTH_VS_STRENGTH", "Strength on strength"
    elif separation >= 0.5 and quality >= 0.4:
        favored = signal.attack_team if difference > 0 else signal.defend_team
        archetype, headline = "MISMATCH", f"{favored} has a clear edge"
    elif quality < 0.3:
        archetype, headline = "LOW_QUALITY", "Neither unit graded well"
    else:
        archetype, headline = "EVEN", "Evenly matched"

    reasons = [
        f"{signal.attack_team} {signal.attack_label.lower()} {attack:.1f}",
        f"{signal.defend_team} {signal.defend_label.lower()} {defend:.1f}",
        f"{abs(difference):.1f} grade points apart",
    ]
    if not signal.confident:
        reasons.append("limited graded sample")

    return {
        "label": signal.label,
        "archetype": archetype,
        "headline": headline,
        "interest": round(interest, 1),
        "attack_team": signal.attack_team,
        "attack_label": signal.attack_label,
        "attack_grade": round(attack, 1),
        "defend_team": signal.defend_team,
        "defend_label": signal.defend_label,
        "defend_grade": round(defend, 1),
        "margin": round(abs(difference), 1),
        "advantage": (signal.attack_team if difference > 0 else signal.defend_team)
                     if abs(difference) >= 2.5 else None,
        "confident": signal.confident,
        "provider": signal.provider,
        "reasons": reasons,
    }


def signals_from_pff(pff_matchups: Iterable[dict[str, Any]], away_team: str,
                     home_team: str) -> list[MatchupSignal]:
    """Adapt the repository PFF comparison packet into neutral signals."""
    signals: list[MatchupSignal] = []
    for matchup in pff_matchups:
        for attacker, defender, direction in (
            (away_team, home_team, matchup.get("away_attacks") or {}),
            (home_team, away_team, matchup.get("home_attacks") or {}),
        ):
            attack, counter = direction.get("attack"), direction.get("counter")
            if not attack or not counter:
                continue
            signals.append(MatchupSignal(
                label=matchup["label"],
                attack_team=attacker,
                attack_label=direction.get("attack_label") or "offense",
                attack_grade=attack.get("grade"),
                attack_players=int(attack.get("players") or 0),
                attack_usage=float(attack.get("usage") or 0),
                defend_team=defender,
                defend_label=direction.get("counter_label") or "defense",
                defend_grade=counter.get("grade"),
                defend_players=int(counter.get("players") or 0),
                defend_usage=float(counter.get("usage") or 0),
            ))
    return signals


def rank_matchups(signals: Sequence[MatchupSignal], limit: int | None = None) -> list[dict[str, Any]]:
    """Score and sort comparisons, most watchable first."""
    scored = [result for result in (score_matchup(signal) for signal in signals) if result]
    scored.sort(key=lambda item: (-item["interest"], item["label"]))
    return scored[:limit] if limit else scored


def game_matchup_report(pff_matchups: Iterable[dict[str, Any]], away_team: str,
                        home_team: str, limit: int | None = None) -> dict[str, Any]:
    """Ranked matchups for one game, with a headline count for the page."""
    ranked = rank_matchups(signals_from_pff(pff_matchups, away_team, home_team), limit)
    marquee = [item for item in ranked if item["archetype"] == "STRENGTH_VS_STRENGTH"]
    mismatches = [item for item in ranked if item["archetype"] == "MISMATCH"]
    return {
        "matchups": ranked,
        "marquee_count": len(marquee),
        "mismatch_count": len(mismatches),
        "top_interest": ranked[0]["interest"] if ranked else 0.0,
    }
