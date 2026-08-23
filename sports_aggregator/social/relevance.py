"""Explainable relevance scoring for ingested content.

Sorting a feed by publication time treats a national personality speculating
about a player exactly like the beat writer who watched him miss practice. This
module scores each item on the factors that actually distinguish them, and keeps
every factor as text so a ranking can be inspected rather than trusted.

    value = reliability x expertise x role x importance x recency x specificity

Nothing here promotes an item to a fact. Scores order what a reader sees; the
role, confidence, and method recorded at ingestion still decide how it is
labeled.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Sequence


#: Topic weight and half-life in days. Half-lives follow the decay windows in the
#: aggregation spec: breaking news is stale in days, recruiting context is not.
TOPIC_PROFILE: dict[str, tuple[float, float]] = {
    "BREAKING_NEWS": (1.00, 1.5),
    "INJURY": (0.95, 3.0),
    "COACHING": (0.88, 7.0),
    "PLAYOFF": (0.85, 7.0),
    "DEPTH_CHART": (0.82, 3.0),
    "TRANSFER_PORTAL": (0.80, 7.0),
    "GAME_PREVIEW": (0.78, 5.0),
    "RANKINGS": (0.72, 5.0),
    "STATISTICAL_ANALYSIS": (0.70, 14.0),
    "SCHEME_ANALYSIS": (0.70, 14.0),
    "RECRUITING": (0.66, 21.0),
    "ROSTER": (0.64, 7.0),
    "GAME_RECAP": (0.62, 3.0),
    "AWARDS": (0.60, 14.0),
    "NFL_DRAFT": (0.60, 30.0),
    "PLAYER_ANALYSIS": (0.60, 14.0),
    "GOVERNANCE": (0.58, 10.0),
    "NIL": (0.56, 10.0),
    "CONFERENCE": (0.54, 10.0),
    "MEDIA": (0.50, 10.0),
    # Topics added once coverage auditing showed half of content unclassified.
    "DISCIPLINE": (0.86, 5.0),
    "SCHEDULE": (0.60, 14.0),
    "OFFSEASON": (0.58, 7.0),
    "SEASON_PREVIEW": (0.56, 10.0),
    "BOWL": (0.55, 14.0),
    "BETTING": (0.52, 3.0),
    "COMMENTARY": (0.42, 5.0),
    "FACILITIES": (0.35, 21.0),
}

#: Fallback for content that matched no topic rule.
DEFAULT_PROFILE = (0.45, 7.0)

#: Which expertise dimension qualifies a source for a topic. A reporter who is
#: excellent on breaking news is not automatically excellent on scheme.
TOPIC_EXPERTISE: dict[str, str] = {
    "BREAKING_NEWS": "breaking_score",
    "INJURY": "team_access_score",
    "DEPTH_CHART": "team_access_score",
    "ROSTER": "team_access_score",
    "TRANSFER_PORTAL": "transfer_score",
    "RECRUITING": "recruiting_score",
    "NFL_DRAFT": "draft_score",
    "AWARDS": "awards_score",
    "STATISTICAL_ANALYSIS": "analytics_score",
    "SCHEME_ANALYSIS": "scheme_score",
    "PLAYER_ANALYSIS": "scheme_score",
    "RANKINGS": "national_score",
    "PLAYOFF": "national_score",
    "CONFERENCE": "national_score",
    "GOVERNANCE": "national_score",
    "MEDIA": "national_score",
    "NIL": "national_score",
    "COACHING": "reporting_score",
    "GAME_PREVIEW": "national_score",
    "GAME_RECAP": "reporting_score",
    "DISCIPLINE": "team_access_score",
    "SCHEDULE": "reporting_score",
    "OFFSEASON": "team_access_score",
    "SEASON_PREVIEW": "national_score",
    "BOWL": "national_score",
    "BETTING": "analytics_score",
    "COMMENTARY": "national_score",
    "FACILITIES": "reporting_score",
}

DEFAULT_EXPERTISE = "reporting_score"

#: How much weight a source role carries. Aggregation and community reaction are
#: kept and shown, but they do not outrank the reporting they point at.
ROLE_WEIGHT: dict[str, float] = {
    "ORIGINAL_REPORT": 1.00,
    "OFFICIAL_CONFIRMATION": 0.92,
    "REPORTING_UNDETERMINED": 0.85,
    "CORROBORATION": 0.72,
    "ANALYSIS": 0.70,
    "COMMENTARY": 0.55,
    "AGGREGATION": 0.45,
    "COMMUNITY_REACTION": 0.38,
    "AUTOMATED": 0.20,
    "UNCLASSIFIED": 0.50,
}

#: Reddit content types that describe the submission better than its role alone.
CONTENT_TYPE_WEIGHT: dict[str, float] = {
    "GAME_THREAD": 0.30,
    "POSTGAME_THREAD": 0.35,
    "QUESTION": 0.25,
    "RUMOR": 0.30,
    "RESOURCE": 0.55,
}


def topic_profile(topics: Iterable[str]) -> tuple[str | None, float, float]:
    """The most important topic on an item, with its weight and half-life."""
    best: tuple[str | None, float, float] = (None, *DEFAULT_PROFILE)
    for topic in topics:
        weight, halflife = TOPIC_PROFILE.get(topic, DEFAULT_PROFILE)
        if weight > best[1]:
            best = (topic, weight, halflife)
    return best


def recency_factor(published_at: str | None, halflife_days: float,
                   now: datetime | None = None) -> float:
    """Exponential decay on the topic half-life, floored so nothing hits zero."""
    if not published_at:
        return 0.35
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.35
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    age_days = max(0.0, (reference - published).total_seconds() / 86400)
    return max(0.05, math.pow(0.5, age_days / max(halflife_days, 0.25)))


def expertise_factor(entity: dict[str, Any] | None, topic: str | None) -> tuple[float, str]:
    """Score the source on the dimension the topic actually calls for."""
    if not entity:
        return 0.5, "unattributed source"
    column = TOPIC_EXPERTISE.get(topic or "", DEFAULT_EXPERTISE)
    raw = entity.get(column)
    if raw is None:
        raw = entity.get(DEFAULT_EXPERTISE) or 0
    label = column.replace("_score", "").replace("_", " ")
    return max(0.2, float(raw) / 5.0), f"{label} expertise {raw}/5"


def specificity_factor(team_confidence: float | None, player_confidence: float | None,
                       game_score: float | None) -> tuple[float, str]:
    """Reward items resolved to a concrete team, player, or scheduled game."""
    if game_score and game_score >= 0.75:
        return 1.0, "linked to a scheduled game"
    if player_confidence and player_confidence >= 0.9:
        return 0.92, "linked to a rostered player"
    if team_confidence and team_confidence >= 0.9:
        return 0.85, "linked to a team by exact alias"
    if team_confidence:
        return 0.7, "team context only"
    return 0.55, "no resolved entity"


def beat_bonus(entity_team_ids: Sequence[int], content_team_ids: Sequence[int]) -> tuple[float, str | None]:
    """A source covering the team in question is better positioned to know."""
    if entity_team_ids and set(entity_team_ids) & set(content_team_ids):
        return 1.15, "source covers this team"
    return 1.0, None


def score_item(item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Score one content row and return the value with its factor breakdown.

    ``item`` carries the content row, its resolved ``topics``, the source entity
    expertise columns, and the best team/player/game confidences.
    """
    topics = list(item.get("topics") or [])
    topic, importance, halflife = topic_profile(topics)
    entity = item.get("entity")
    reliability = max(0.2, float((entity or {}).get("reliability_score") or 2) / 5.0)
    expertise, expertise_note = expertise_factor(entity, topic)
    role = item.get("source_role") or "UNCLASSIFIED"
    role_weight = ROLE_WEIGHT.get(role, 0.5)
    content_type = item.get("content_type") or ""
    role_weight = min(role_weight, CONTENT_TYPE_WEIGHT.get(content_type, 1.0))
    recency = recency_factor(item.get("published_at"), halflife, now)
    specificity, specificity_note = specificity_factor(
        item.get("team_confidence"), item.get("player_confidence"), item.get("game_score"))
    bonus, bonus_note = beat_bonus(item.get("entity_team_ids") or [],
                                   item.get("content_team_ids") or [])
    raw = reliability * expertise * role_weight * importance * recency * specificity * bonus
    factors = [
        f"reliability {(entity or {}).get('reliability_score', '—')}/5",
        expertise_note,
        f"{role.replace('_', ' ').lower()} role",
        f"topic {topic or 'unclassified'}",
        f"{_age_label(item.get('published_at'), now)}",
        specificity_note,
    ]
    if bonus_note:
        factors.append(bonus_note)
    return {
        "score": round(min(100.0, raw * 100), 1),
        "topic": topic,
        "importance": round(importance, 3),
        "recency": round(recency, 3),
        "expertise": round(expertise, 3),
        "specificity": round(specificity, 3),
        "factors": factors,
    }


def _age_label(published_at: str | None, now: datetime | None = None) -> str:
    if not published_at:
        return "undated"
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return "undated"
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    delta = (now or datetime.now(timezone.utc)) - published
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "minutes old"
    if hours < 24:
        return f"{int(hours)}h old"
    return f"{int(hours // 24)}d old"
