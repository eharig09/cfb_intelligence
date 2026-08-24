"""Validation and ingestion for YouTube channels and podcast feeds.

Search rank is not identity. A query for "Split Zone Duo" returns a four
subscriber channel above the real show, and "Joel Klatt Show" returns a channel
with no videos at all. Promoting the first hit would put impersonators and clip
mirrors into the trusted registry.

So candidates are scored on evidence that is hard to fake in combination -- name
agreement, audience size, publishing history, and college-football vocabulary in
the channel description -- and only clear matches are promoted. Everything else
is retained for review with the reason recorded.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

import requests

from sports_aggregator.social.unified import UnifiedSourceRegistry


#: Vocabulary that a genuine college-football channel description tends to use.
CFB_VOCABULARY = re.compile(
    r"\b(college football|cfb|ncaa|fbs|playoff|recruiting|big ten|sec|acc|big 12|"
    r"pac-12|mountain west|conference usa|sun belt|american athletic|heisman)\b", re.I)

#: A channel below this many subscribers is not treated as an established show.
MIN_SUBSCRIBERS = 5_000
#: A channel below this many uploads has no publishing history to judge.
MIN_VIDEOS = 25
#: Promotion threshold. Below this a candidate stays in review.
PROMOTION_SCORE = 0.75


def _normalize(value: str) -> str:
    """Lowercase alphanumeric form used for name comparison."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def name_agreement(candidate: str, channel_title: str) -> float:
    """How strongly a channel title agrees with the candidate name.

    Shows routinely append descriptive suffixes -- "Split Zone Duo College
    Football", "The Joel Klatt Show: A College Football Podcast" -- so a
    containment match counts, while a partial word overlap does not.
    """
    left, right = _normalize(candidate), _normalize(channel_title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.9
    left_words, right_words = set(left.split()), set(right.split())
    if not left_words:
        return 0.0
    overlap = len(left_words & right_words) / len(left_words)
    return overlap * 0.6


def score_channel(candidate_name: str, channel: dict[str, Any], *,
                  curated_identity: bool = False) -> dict[str, Any]:
    """Score one search result against the candidate, with reasons."""
    agreement = name_agreement(candidate_name, channel.get("title", ""))
    subscribers = int(channel.get("subscribers") or 0)
    videos = int(channel.get("videos") or 0)
    description = channel.get("description") or ""
    topical = bool(CFB_VOCABULARY.search(description) or
                   CFB_VOCABULARY.search(channel.get("title") or ""))

    audience = min(1.0, subscribers / 50_000) if subscribers else 0.0
    history = min(1.0, videos / 200) if videos else 0.0
    score = (0.55 * agreement) + (0.2 * audience) + (0.15 * history) + (0.1 * topical)

    reasons = [f"name agreement {agreement:.2f}",
               f"{subscribers:,} subscribers", f"{videos:,} uploads",
               "college-football vocabulary" if topical else "no CFB vocabulary in description"]
    blockers = []
    if agreement < 0.85:
        blockers.append("channel title does not clearly match the candidate")
    # Search-only discovery still needs an audience/history corroborator because
    # names are easy to impersonate.  A researched stable URL does not: small
    # local channels are judged on identity, activity and access evidence.
    if subscribers < MIN_SUBSCRIBERS and not curated_identity:
        blockers.append(f"under {MIN_SUBSCRIBERS:,} subscribers")
    minimum_videos = 5 if curated_identity else MIN_VIDEOS
    if videos < minimum_videos:
        blockers.append(f"under {minimum_videos} uploads")
    threshold = 0.55 if curated_identity else PROMOTION_SCORE
    return {
        "channel_id": channel.get("channel_id"),
        "title": channel.get("title"),
        "handle": channel.get("handle"),
        "subscribers": subscribers,
        "videos": videos,
        "uploads_playlist": channel.get("uploads_playlist"),
        "score": round(score, 3),
        "reasons": reasons,
        "blockers": blockers,
        "promotable": not blockers and score >= threshold,
    }


class PodcastDirectoryClient:
    """Look up podcast feed URLs through the public iTunes search directory.

    Feed URLs are not guessable and must not be invented. The directory is used
    only to discover a candidate URL; the feed itself is then fetched and its
    own title is what establishes identity.
    """

    def __init__(self, base_url="https://itunes.apple.com", session=None, timeout=15) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def search(self, term: str, limit: int = 5) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/search",
            params={"term": term, "media": "podcast", "entity": "podcast", "limit": limit},
            headers={"User-Agent": "sports-news-aggregator/1.0 source-validator"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        results = (response.json() or {}).get("results") or []
        return [{
            "name": str(item.get("collectionName") or ""),
            "artist": str(item.get("artistName") or ""),
            "feed_url": str(item.get("feedUrl") or ""),
            "genres": [str(value) for value in (item.get("genres") or [])],
            "episode_count": int(item.get("trackCount") or 0),
        } for item in results if item.get("feedUrl")]


def score_podcast(candidate_name: str, show: dict[str, Any]) -> dict[str, Any]:
    """Score a directory result against the candidate show name."""
    agreement = name_agreement(candidate_name, show.get("name", ""))
    episodes = int(show.get("episode_count") or 0)
    genres = " ".join(show.get("genres") or [])
    topical = bool(CFB_VOCABULARY.search(f"{show.get('name', '')} {show.get('artist', '')}")
                   or "Sport" in genres)
    history = min(1.0, episodes / 200) if episodes else 0.0
    score = (0.65 * agreement) + (0.2 * history) + (0.15 * topical)
    blockers = []
    if agreement < 0.85:
        blockers.append("feed title does not clearly match the candidate")
    if episodes < 20:
        blockers.append("under 20 published episodes")
    if not show.get("feed_url", "").startswith(("http://", "https://")):
        blockers.append("no usable feed URL")
    return {
        "channel_id": show.get("feed_url"),
        "title": show.get("name"),
        "handle": show.get("artist") or "",
        "subscribers": 0,
        "videos": episodes,
        "uploads_playlist": "",
        "score": round(score, 3),
        "reasons": [f"name agreement {agreement:.2f}", f"{episodes} episodes",
                    "sports genre" if topical else "no sports genre"],
        "blockers": blockers,
        "promotable": not blockers and score >= PROMOTION_SCORE,
    }


MEDIA_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_candidate_matches (
 candidate_id INTEGER NOT NULL, platform TEXT NOT NULL, platform_id TEXT NOT NULL,
 display_name TEXT NOT NULL, handle TEXT NOT NULL DEFAULT '',
 score REAL NOT NULL, promotable INTEGER NOT NULL,
 evidence_json TEXT NOT NULL, checked_at TEXT NOT NULL,
 PRIMARY KEY(candidate_id,platform,platform_id)
);
CREATE TABLE IF NOT EXISTS media_validation_attempts (
 candidate_id INTEGER NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL,
 PRIMARY KEY(candidate_id,platform)
);
CREATE TABLE IF NOT EXISTS content_clusters (
 cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
 cluster_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 source_entity_id INTEGER, first_published_at TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_cluster_items (
 cluster_id INTEGER NOT NULL, content_id INTEGER NOT NULL, platform TEXT NOT NULL,
 PRIMARY KEY(cluster_id,content_id)
);
"""


class MediaRegistry:
    """Validate and promote YouTube channels and podcast feeds."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        UnifiedSourceRegistry(self.path).initialize()
        with closing(self._connect()) as connection:
            connection.executescript(MEDIA_SCHEMA)

    def pending_candidates(self, platform: str | None = None, *,
                           force: bool = False, retry_after_days: int = 7) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT c.*,p.source_key,p.team,p.conference,p.coverage,
                          p.platform_json,p.tags_json,p.priority catalog_priority,
                          p.youtube_url,p.podcast_url,p.podcast_page_url,p.website,
                          p.active_status,p.last_verified_active,p.subscriber_count,
                          p.episode_frequency,p.original_reporting,p.program_access,
                          p.reporting_evidence,p.content_focus,p.notes,p.catalog_status
                   FROM source_candidates c
                   LEFT JOIN media_source_profiles p USING(candidate_id)
                   WHERE c.validation_status IN ('SOURCE_CANDIDATE','NEEDS_REVIEW','PROMOTED')
                   ORDER BY c.candidate_id""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["platforms"] = json.loads(item.pop("platform_json") or "[]")
                item["tags"] = json.loads(item.pop("tags_json") or "[]")
                if platform:
                    entity_key = f"show:{_normalize(item['name']).replace(' ', '-')}"
                    exists = connection.execute(
                        """SELECT 1 FROM source_endpoints ep JOIN source_entities e
                           USING(source_entity_id) WHERE e.entity_key=? AND ep.platform=?
                           AND ep.active=1 AND ep.verification_status='verified'""",
                        (entity_key, platform),
                    ).fetchone()
                    if exists:
                        continue
                    attempt = connection.execute(
                        """SELECT checked_at FROM media_validation_attempts
                           WHERE candidate_id=? AND platform=?""",
                        (item["candidate_id"], platform),
                    ).fetchone()
                    if attempt and not force:
                        checked = datetime.fromisoformat(attempt["checked_at"])
                        age = datetime.now(timezone.utc) - checked.astimezone(timezone.utc)
                        if age.days < max(retry_after_days, 1):
                            continue
                result.append(item)
            return result

    def record_attempt(self, candidate_id: int, platform: str,
                       status: str, notes: str = "") -> None:
        self.initialize(); now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO media_validation_attempts VALUES(?,?,?,?,?)
                   ON CONFLICT(candidate_id,platform) DO UPDATE SET
                   status=excluded.status,notes=excluded.notes,checked_at=excluded.checked_at""",
                (candidate_id, platform, status, notes[:1000], now),
            )
            connection.commit()

    @staticmethod
    def _enrich_entity(connection: sqlite3.Connection, entity_id: int,
                       candidate: dict[str, Any], classes: Iterable[str]) -> None:
        tags = set(candidate.get("tags") or [])
        priority = int(candidate.get("catalog_priority") or 2)
        reporting = 4 if candidate.get("original_reporting") else (2 if "intel" in tags else 1)
        access = 4 if candidate.get("program_access") else (2 if candidate.get("team") else 1)
        analytics = 4 if "analysis" in tags else 1
        national = 4 if not candidate.get("team") and not candidate.get("conference") else 1
        g5 = 5 if candidate.get("conference") in {
            "American Athletic", "Conference USA", "Mid-American", "Mountain West",
            "Pac-12", "Sun Belt",
        } or "G6" in (candidate.get("coverage") or "") else 1
        connection.execute(
            """UPDATE source_entities SET priority=max(priority,?),
                 reporting_score=max(reporting_score,?),team_access_score=max(team_access_score,?),
                 analytics_score=max(analytics_score,?),national_score=max(national_score,?),
                 g5_score=max(g5_score,?),updated_at=? WHERE source_entity_id=?""",
            (priority, reporting, access, analytics, national, g5,
             datetime.now(timezone.utc).isoformat(), entity_id),
        )
        for source_class in classes:
            connection.execute("INSERT OR IGNORE INTO source_entity_classes VALUES(?,?)",
                               (entity_id, source_class))
        for specialty in sorted(tags | {item for item in (candidate.get("content_focus") or "").split(", ") if item}):
            connection.execute("INSERT OR IGNORE INTO source_entity_specialties VALUES(?,?)",
                               (entity_id, specialty))
        if candidate.get("team"):
            connection.execute("INSERT OR REPLACE INTO source_entity_teams VALUES(?,?,1.0)",
                               (entity_id, candidate["team"]))
        if candidate.get("conference"):
            connection.execute("INSERT OR REPLACE INTO source_entity_conferences VALUES(?,?,1.0)",
                               (entity_id, candidate["conference"]))

    def record_matches(self, candidate_id: int, platform: str,
                       matches: Iterable[dict[str, Any]]) -> None:
        """Persist every scored match, promotable or not, for auditability."""
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            for match in matches:
                connection.execute(
                    """INSERT INTO media_candidate_matches
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(candidate_id,platform,platform_id) DO UPDATE SET
                       display_name=excluded.display_name,handle=excluded.handle,
                       score=excluded.score,promotable=excluded.promotable,
                       evidence_json=excluded.evidence_json,checked_at=excluded.checked_at""",
                    (candidate_id, platform, match["channel_id"], match["title"] or "",
                     match.get("handle") or "", match["score"], int(match["promotable"]),
                     json.dumps({"reasons": match["reasons"], "blockers": match["blockers"],
                                 "subscribers": match["subscribers"], "videos": match["videos"]},
                                separators=(",", ":")), now))
            connection.commit()

    def promote_channel(self, candidate: dict[str, Any], match: dict[str, Any]) -> int:
        """Create the source entity and YouTube endpoint for a validated match."""
        if not match.get("promotable"):
            raise ValueError("Refusing to promote a channel that did not pass validation")
        now = datetime.now(timezone.utc).isoformat()
        name = candidate["name"]
        entity_key = f"show:{_normalize(name).replace(' ', '-')}"
        classes = [item.strip() for item in (candidate.get("proposed_classes") or "").split(",")
                   if item.strip()]
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO source_entities(entity_key,name,organization,entity_type,
                   reliability_score,reporting_score,team_access_score,national_score,
                   analytics_score,scheme_score,recruiting_score,transfer_score,draft_score,
                   awards_score,g5_score,breaking_score,official_score,priority,trust_status,
                   active,updated_at)
                   VALUES(?,?,'',?,3,2,1,3,2,3,1,1,1,1,2,1,0,2,'VALIDATED_MEDIA',1,?)
                   ON CONFLICT(entity_key) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at""",
                (entity_key, name, candidate.get("proposed_entity_type") or "SHOW", now))
            entity_id = connection.execute(
                "SELECT source_entity_id FROM source_entities WHERE entity_key=?",
                (entity_key,)).fetchone()[0]
            self._enrich_entity(connection, entity_id, candidate, classes)
            endpoint_key = f"youtube:channel:{match['channel_id']}"
            connection.execute(
                """INSERT INTO source_endpoints(source_entity_id,endpoint_key,platform,
                   endpoint_type,handle,platform_id,url,active,verification_status,
                   verified_at,last_checked_at,last_success_at,display_name,description,updated_at)
                   VALUES(?,?,'youtube','YOUTUBE_CHANNEL',?,?,?,1,'verified',?,?,?,?,'',?)
                   ON CONFLICT(endpoint_key) DO UPDATE SET
                   source_entity_id=excluded.source_entity_id,platform_id=excluded.platform_id,
                   verification_status='verified',verified_at=excluded.verified_at,
                   last_checked_at=excluded.last_checked_at,display_name=excluded.display_name,
                   updated_at=excluded.updated_at""",
                (entity_id, endpoint_key, match.get("handle") or "", match["channel_id"],
                 f"https://www.youtube.com/channel/{match['channel_id']}",
                 now, now, now, match["title"] or name, now))
            connection.execute(
                """UPDATE source_candidates SET validation_status='PROMOTED',
                   validation_score=?,validation_notes=?,last_checked_at=?
                   WHERE candidate_id=?""",
                (match["score"], f"YouTube channel {match['channel_id']} validated",
                 now, candidate["candidate_id"]))
            connection.commit()
        return entity_id

    def promote_podcast(self, candidate: dict[str, Any], match: dict[str, Any],
                        feed_title: str) -> int:
        """Attach a validated feed to the show entity that already exists.

        A show is one source entity. Its YouTube channel and its podcast feed are
        two endpoints on that entity, so distributing the same episode through
        both cannot double its apparent authority.
        """
        if not match.get("promotable"):
            raise ValueError("Refusing to promote a feed that did not pass validation")
        now = datetime.now(timezone.utc).isoformat()
        name = candidate["name"]
        entity_key = f"show:{_normalize(name).replace(' ', '-')}"
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT source_entity_id FROM source_entities WHERE entity_key=?",
                (entity_key,)).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO source_entities(entity_key,name,organization,entity_type,
                       reliability_score,reporting_score,team_access_score,national_score,
                       analytics_score,scheme_score,recruiting_score,transfer_score,draft_score,
                       awards_score,g5_score,breaking_score,official_score,priority,trust_status,
                       active,updated_at)
                       VALUES(?,?,'',?,3,2,1,3,2,3,1,1,1,1,2,1,0,2,'VALIDATED_MEDIA',1,?)""",
                    (entity_key, name, candidate.get("proposed_entity_type") or "SHOW", now))
                row = connection.execute(
                    "SELECT source_entity_id FROM source_entities WHERE entity_key=?",
                    (entity_key,)).fetchone()
            entity_id = row[0]
            self._enrich_entity(connection, entity_id, candidate, ("PODCAST",))
            endpoint_key = f"podcast:feed:{_normalize(name).replace(' ', '-')}"
            connection.execute(
                """INSERT INTO source_endpoints(source_entity_id,endpoint_key,platform,
                   endpoint_type,handle,platform_id,url,active,verification_status,
                   verified_at,last_checked_at,last_success_at,display_name,description,updated_at)
                   VALUES(?,?,'podcast','PODCAST_RSS','',?,?,1,'verified',?,?,?,?,'',?)
                   ON CONFLICT(endpoint_key) DO UPDATE SET platform_id=excluded.platform_id,
                   url=excluded.url,verification_status='verified',verified_at=excluded.verified_at,
                   last_checked_at=excluded.last_checked_at,display_name=excluded.display_name,
                   updated_at=excluded.updated_at""",
                (entity_id, endpoint_key, match["channel_id"], match["channel_id"],
                 now, now, now, feed_title or name, now))
            connection.execute(
                """UPDATE source_candidates SET validation_score=?,
                   validation_status='PROMOTED',
                   validation_notes=validation_notes||' | podcast feed validated',
                   last_checked_at=? WHERE candidate_id=?""",
                (match["score"], now, candidate["candidate_id"]))
            connection.commit()
        return entity_id

    def mark_needs_review(self, candidate_id: int, note: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """UPDATE source_candidates SET validation_status='NEEDS_REVIEW',
                   validation_notes=?,last_checked_at=? WHERE candidate_id=?""",
                (note, now, candidate_id))
            connection.commit()

    def youtube_endpoints(self) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT ep.*,e.name,e.entity_type FROM source_endpoints ep
                   JOIN source_entities e USING(source_entity_id)
                   WHERE ep.platform='youtube' AND ep.active=1
                   AND ep.verification_status='verified' AND ep.platform_id IS NOT NULL
                   ORDER BY e.name""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["classes"] = {inner[0] for inner in connection.execute(
                    "SELECT source_class FROM source_entity_classes WHERE source_entity_id=?",
                    (row["source_entity_id"],))}
                result.append(item)
        return result

    def podcast_endpoints(self) -> list[dict[str, Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT ep.*,e.name,e.entity_type FROM source_endpoints ep
                   JOIN source_entities e USING(source_entity_id)
                   WHERE ep.platform='podcast' AND ep.active=1
                   AND ep.verification_status='verified' AND ep.platform_id IS NOT NULL
                   ORDER BY e.name""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["classes"] = {inner[0] for inner in connection.execute(
                    "SELECT source_class FROM source_entity_classes WHERE source_entity_id=?",
                    (row["source_entity_id"],))}
                result.append(item)
        return result

    def status(self) -> dict[str, Any]:
        self.initialize()
        with closing(self._connect()) as connection:
            candidates = [dict(row) for row in connection.execute(
                """SELECT c.*,p.source_key,p.team,p.conference,p.coverage,
                          p.platform_json,p.tags_json,p.priority catalog_priority,
                          p.youtube_url,p.podcast_url,p.podcast_page_url,p.website,
                          p.active_status,p.last_verified_active,p.subscriber_count,
                          p.episode_frequency,p.original_reporting,p.program_access,
                          p.reporting_evidence,p.content_focus,p.notes,p.catalog_status
                   FROM source_candidates c LEFT JOIN media_source_profiles p USING(candidate_id)
                   ORDER BY c.validation_status,c.name""")]
            matches = [dict(row) for row in connection.execute(
                """SELECT m.*,c.name candidate_name FROM media_candidate_matches m
                   JOIN source_candidates c USING(candidate_id)
                   ORDER BY m.candidate_id,m.score DESC""")]
            attempts = [dict(row) for row in connection.execute(
                """SELECT a.*,c.name candidate_name FROM media_validation_attempts a
                   JOIN source_candidates c USING(candidate_id)
                   ORDER BY a.checked_at DESC,c.name""")]
        for match in matches:
            match["evidence"] = json.loads(match.pop("evidence_json") or "{}")
        for candidate in candidates:
            candidate["platforms"] = json.loads(candidate.pop("platform_json") or "[]")
            candidate["tags"] = json.loads(candidate.pop("tags_json") or "[]")
        return {"candidates": candidates, "matches": matches, "attempts": attempts,
                "youtube_endpoints": self.youtube_endpoints(),
                "podcast_endpoints": self.podcast_endpoints()}
