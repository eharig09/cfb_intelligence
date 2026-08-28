"""Small admin-facing helpers for manually seeding trusted source candidates."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
from urllib.parse import urlparse
from typing import Any

from sports_aggregator.social.models import SourceProfile


ALLOWED_PLATFORMS = {"website", "rss", "youtube", "podcast", "bluesky"}
SEED_TAGS = {"intel", "analysis", "betting", "sentiment"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or "").split(",")
    return list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _normalized_endpoint(platform: str, raw: str) -> tuple[str | None, str | None, str, str]:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("A URL or handle is required.")
    platform = platform.casefold().strip()
    if platform not in ALLOWED_PLATFORMS:
        raise ValueError("Unsupported source platform.")

    if platform == "bluesky":
        handle = value.removeprefix("@").strip()
        if "bsky.app/profile/" in handle:
            handle = handle.split("bsky.app/profile/", 1)[1].split("/", 1)[0]
        handle = handle.strip("/")
        if not handle or "." not in handle:
            raise ValueError("Enter a Bluesky handle or profile URL.")
        url = f"https://bsky.app/profile/{handle}"
        return handle, None, url, f"bluesky:account:{handle.casefold()}"

    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("Enter a valid URL.")
    normalized_url = parsed.geturl()

    if platform == "youtube":
        handle = None
        path = parsed.path.strip("/")
        if path.startswith("@"):
            handle = path.split("/", 1)[0]
        return handle, None, normalized_url, f"youtube:url:{normalized_url.casefold()}"
    if platform == "podcast":
        return None, None, normalized_url, f"podcast:feed:{normalized_url.casefold()}"
    if platform == "rss":
        return None, None, normalized_url, f"rss:feed:{normalized_url.casefold()}"
    return None, None, normalized_url, f"website:url:{normalized_url.casefold()}"


def _entity_defaults(tags: set[str]) -> dict[str, int]:
    return {
        "reliability_score": 3,
        "reporting_score": 4 if "intel" in tags else 2,
        "team_access_score": 3 if "intel" in tags else 1,
        "national_score": 1,
        "analytics_score": 4 if "analysis" in tags else (3 if "betting" in tags else 1),
        "scheme_score": 3 if "analysis" in tags else 1,
        "recruiting_score": 2 if "intel" in tags else 1,
        "transfer_score": 2 if "intel" in tags else 1,
        "draft_score": 1,
        "awards_score": 1,
        "g5_score": 1,
        "breaking_score": 3 if "intel" in tags else 1,
        "official_score": 0,
    }


def add_or_update_source(unified_registry, legacy_registry, payload: dict[str, Any]) -> dict[str, Any]:
    """Add one endpoint and additive seed metadata to the source graph.

    Exact case-insensitive source-name matches reuse the existing entity so a
    website, podcast, YouTube channel and Bluesky account can all belong to the
    same source. New endpoints remain seeded/unverified until normal resolvers
    validate them.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Source name is required.")
    platform = str(payload.get("platform") or "").casefold().strip()
    endpoint_value = str(payload.get("endpoint") or payload.get("url") or "").strip()
    priority = int(payload.get("priority") or 3)
    if priority < 1 or priority > 5:
        raise ValueError("Priority must be between 1 and 5.")
    entity_type = str(payload.get("entity_type") or "SOURCE").strip().upper()
    seed_tags = set(_clean_list(payload.get("tags")))
    seed_tags |= {tag for tag in SEED_TAGS if payload.get(tag) in (True, "true", "1", 1, "on")}
    teams = _clean_list(payload.get("teams") or payload.get("team"))
    conferences = _clean_list(payload.get("conferences") or payload.get("conference"))
    handle, platform_id, url, endpoint_key = _normalized_endpoint(platform, endpoint_value)
    endpoint_type = {
        "website": "WEBSITE",
        "rss": "FEED",
        "youtube": "CHANNEL",
        "podcast": "PODCAST_FEED",
        "bluesky": "ACCOUNT",
    }[platform]
    classes = {
        "website": {"REPORTING"},
        "rss": {"REPORTING"},
        "youtube": {"MEDIA"},
        "podcast": {"MEDIA"},
        "bluesky": {"SOCIAL"},
    }[platform]
    if "intel" in seed_tags:
        classes.add("REPORTING")
    if "analysis" in seed_tags or "betting" in seed_tags:
        classes.add("ANALYSIS")

    unified_registry.initialize()
    now = _now()
    with closing(unified_registry._connect()) as db:
        existing = db.execute(
            "SELECT * FROM source_entities WHERE lower(name)=lower(?) ORDER BY active DESC, priority DESC LIMIT 1",
            (name,),
        ).fetchone()
        if existing:
            entity_id = int(existing["source_entity_id"])
            defaults = _entity_defaults(seed_tags)
            db.execute(
                """UPDATE source_entities SET priority=max(priority,?), active=1,
                   reporting_score=max(reporting_score,?), team_access_score=max(team_access_score,?),
                   analytics_score=max(analytics_score,?), scheme_score=max(scheme_score,?),
                   recruiting_score=max(recruiting_score,?), transfer_score=max(transfer_score,?),
                   breaking_score=max(breaking_score,?), updated_at=? WHERE source_entity_id=?""",
                (priority, defaults["reporting_score"], defaults["team_access_score"],
                 defaults["analytics_score"], defaults["scheme_score"],
                 defaults["recruiting_score"], defaults["transfer_score"],
                 defaults["breaking_score"], now, entity_id),
            )
        else:
            defaults = _entity_defaults(seed_tags)
            entity_key = f"manual:{_slug(name)}"
            suffix = 2
            while db.execute("SELECT 1 FROM source_entities WHERE entity_key=?", (entity_key,)).fetchone():
                entity_key = f"manual:{_slug(name)}-{suffix}"; suffix += 1
            db.execute(
                """INSERT INTO source_entities(
                   entity_key,name,organization,entity_type,reliability_score,reporting_score,
                   team_access_score,national_score,analytics_score,scheme_score,recruiting_score,
                   transfer_score,draft_score,awards_score,g5_score,breaking_score,official_score,
                   priority,trust_status,active,last_verified_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entity_key, name, payload.get("organization"), entity_type,
                 defaults["reliability_score"], defaults["reporting_score"],
                 defaults["team_access_score"], defaults["national_score"],
                 defaults["analytics_score"], defaults["scheme_score"],
                 defaults["recruiting_score"], defaults["transfer_score"],
                 defaults["draft_score"], defaults["awards_score"], defaults["g5_score"],
                 defaults["breaking_score"], defaults["official_score"], priority,
                 "TRUSTED_SEED", 1, None, now),
            )
            entity_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        for source_class in sorted(classes):
            db.execute("INSERT OR IGNORE INTO source_entity_classes VALUES(?,?)", (entity_id, source_class))
        for specialty in sorted(seed_tags):
            db.execute("INSERT OR IGNORE INTO source_entity_specialties VALUES(?,?)", (entity_id, specialty))
        for team in teams:
            db.execute("INSERT OR REPLACE INTO source_entity_teams VALUES(?,?,1.0)", (entity_id, team))
        for conference in conferences:
            db.execute("INSERT OR REPLACE INTO source_entity_conferences VALUES(?,?,1.0)", (entity_id, conference))
        db.execute(
            """INSERT INTO source_endpoints(source_entity_id,endpoint_key,platform,endpoint_type,
               handle,platform_id,url,active,verification_status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(endpoint_key) DO UPDATE SET
               source_entity_id=excluded.source_entity_id,handle=COALESCE(excluded.handle,source_endpoints.handle),
               url=COALESCE(excluded.url,source_endpoints.url),active=1,updated_at=excluded.updated_at""",
            (entity_id, endpoint_key, platform, endpoint_type, handle, platform_id, url, 1,
             "seeded_unverified", now),
        )
        endpoint_id = int(db.execute(
            "SELECT endpoint_id FROM source_endpoints WHERE endpoint_key=?", (endpoint_key,)
        ).fetchone()[0])
        db.commit()

    # Bluesky ingestion still consumes the original handle registry, so mirror a
    # manual Bluesky seed there until all callers are unified-source native.
    if platform == "bluesky" and legacy_registry is not None and handle:
        defaults = _entity_defaults(seed_tags)
        legacy_registry.seed((SourceProfile(
            handle=handle,
            display_name=name,
            organization=str(payload.get("organization") or "").strip() or None,
            source_type=entity_type,
            specialties=tuple(sorted(seed_tags)),
            conferences=tuple(conferences), teams=tuple(teams),
            reliability=defaults["reliability_score"],
            original_reporting_score=defaults["reporting_score"],
            analysis_score=defaults["analytics_score"],
            breaking_news_score=defaults["breaking_score"],
            prospect_score=1, g5_score=1, priority=priority, active=True,
        )),))

    return {
        "source_entity_id": entity_id,
        "endpoint_id": endpoint_id,
        "name": name,
        "platform": platform,
        "endpoint": url,
        "tags": sorted(seed_tags),
        "teams": teams,
        "conferences": conferences,
        "priority": priority,
        "verification_status": "seeded_unverified",
    }
