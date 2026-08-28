"""Admin helpers for viewing and replacing unified source seed tags."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from typing import Any

from sports_aggregator.social.source_admin import SEED_TAGS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tags(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        tag = str(item).strip().casefold()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def source_tag_catalog(unified_registry, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Search active source entities with current specialties and endpoints."""
    unified_registry.initialize()
    query = str(query or "").strip()
    limit = max(1, min(int(limit or 100), 250))
    with closing(unified_registry._connect()) as db:
        params: list[Any] = []
        where = "WHERE e.active=1"
        if query:
            where += " AND (lower(e.name) LIKE lower(?) OR lower(COALESCE(e.organization,'')) LIKE lower(?) OR EXISTS (SELECT 1 FROM source_endpoints x WHERE x.source_entity_id=e.source_entity_id AND lower(COALESCE(x.url,x.handle,'')) LIKE lower(?)))"
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        params.append(limit)
        entities = [dict(row) for row in db.execute(
            f"""SELECT e.source_entity_id,e.name,e.organization,e.entity_type,e.priority,e.trust_status
                FROM source_entities e {where}
                ORDER BY e.priority DESC, lower(e.name) LIMIT ?""",
            tuple(params),
        )]
        for entity in entities:
            entity_id = entity["source_entity_id"]
            entity["tags"] = [row[0] for row in db.execute(
                "SELECT specialty FROM source_entity_specialties WHERE source_entity_id=? ORDER BY specialty",
                (entity_id,),
            )]
            entity["endpoints"] = [dict(row) for row in db.execute(
                """SELECT platform,COALESCE(url,handle,'') endpoint,verification_status
                   FROM source_endpoints WHERE source_entity_id=? AND active=1
                   ORDER BY platform,endpoint""",
                (entity_id,),
            )]
    return entities


def replace_source_tags(unified_registry, *, source_entity_id: int, tags: Any) -> dict[str, Any]:
    """Replace one source entity's specialty tags without changing identity metadata."""
    unified_registry.initialize()
    entity_id = int(source_entity_id)
    cleaned = _clean_tags(tags)
    now = _now()
    with closing(unified_registry._connect()) as db:
        entity = db.execute(
            "SELECT source_entity_id,name FROM source_entities WHERE source_entity_id=?",
            (entity_id,),
        ).fetchone()
        if entity is None:
            raise ValueError("Source not found.")
        db.execute("DELETE FROM source_entity_specialties WHERE source_entity_id=?", (entity_id,))
        db.executemany(
            "INSERT INTO source_entity_specialties(source_entity_id,specialty) VALUES(?,?)",
            [(entity_id, tag) for tag in cleaned],
        )
        db.execute("UPDATE source_entities SET updated_at=? WHERE source_entity_id=?", (now, entity_id))

        # Manual YouTube/podcast seeds also keep a denormalized tag cache.
        # Align that cache without touching platform identity or verification.
        media_rows = db.execute(
            """SELECT m.candidate_id FROM media_source_profiles m
               JOIN source_candidates c ON c.candidate_id=m.candidate_id
               WHERE lower(c.name)=lower(?)""",
            (entity["name"],),
        ).fetchall()
        db.executemany(
            "UPDATE media_source_profiles SET tags_json=?,content_focus=?,updated_at=? WHERE candidate_id=?",
            [(json.dumps(cleaned), ", ".join(cleaned), now, row["candidate_id"]) for row in media_rows],
        )
        db.commit()

    return {
        "source_entity_id": entity_id,
        "name": entity["name"],
        "tags": cleaned,
        "seed_tags": sorted(tag for tag in cleaned if tag in SEED_TAGS),
    }
