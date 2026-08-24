"""Versioned, machine-readable CFB podcast and video source catalog."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable


CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "media" / "cfb_media_registry.json"
ALLOWED_TAGS = {"intel", "analysis", "betting", "sentiment"}
ALLOWED_PLATFORMS = {"YouTube", "podcast", "website"}
PROFILE_FIELDS = (
    "source", "team", "conference", "coverage", "platform", "youtube_url",
    "podcast_url", "podcast_page_url", "website", "active_status",
    "last_verified_active", "tags", "priority", "subscriber_count",
    "episode_frequency", "original_reporting", "program_access",
    "reporting_evidence", "content_focus", "notes", "status",
)


def source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _record(value: dict[str, Any], *, default_status: str) -> dict[str, Any]:
    record = {field: value.get(field) for field in PROFILE_FIELDS}
    record["source"] = str(record["source"] or "").strip()
    record["platform"] = list(record["platform"] or [])
    record["tags"] = list(record["tags"] or [])
    record["priority"] = int(record["priority"] or 1)
    record["status"] = str(record["status"] or default_status)
    record["source_key"] = source_key(record["source"])
    return record


def load_catalog(path: str | Path = CATALOG_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("media catalog must be a JSON object")
    sections = {
        "seed_sources": "existing",
        "new_sources": "new",
        "seed_source_enrichment": "existing",
    }
    records: dict[str, dict[str, Any]] = {}
    for section, default_status in sections.items():
        values = payload.get(section) or []
        if not isinstance(values, list):
            raise ValueError(f"{section} must be a JSON list")
        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError(f"{section} entries must be JSON objects")
            record = _record(raw, default_status=default_status)
            if not record["source"]:
                raise ValueError(f"{section} contains an unnamed source")
            if not 1 <= record["priority"] <= 5:
                raise ValueError(f"{record['source']}: priority must be 1-5")
            unknown_tags = set(record["tags"]) - ALLOWED_TAGS
            unknown_platforms = set(record["platform"]) - ALLOWED_PLATFORMS
            if unknown_tags or unknown_platforms:
                raise ValueError(
                    f"{record['source']}: unknown tags/platforms "
                    f"{sorted(unknown_tags | unknown_platforms)}"
                )
            key = record["source_key"]
            if key in records:
                # Enrichment is intentionally a partial overlay on the canonical
                # seed record, never a second source identity.
                records[key].update({
                    field: record[field]
                    for field in PROFILE_FIELDS
                    if field in raw and record[field] not in (None, [], "")
                })
            else:
                records[key] = record
    payload["sources"] = sorted(records.values(), key=lambda row: row["source"].casefold())
    return payload


def catalog_sources(path: str | Path = CATALOG_PATH) -> list[dict[str, Any]]:
    return load_catalog(path)["sources"]


def proposed_classes(record: dict[str, Any]) -> tuple[str, ...]:
    classes: list[str] = []
    if "podcast" in record["platform"]:
        classes.append("PODCAST")
    if "YouTube" in record["platform"]:
        classes.append("YOUTUBE_SHOW")
    if "website" in record["platform"]:
        classes.append("PUBLICATION")
    if "betting" in record["tags"]:
        classes.append("MODEL")
    return tuple(dict.fromkeys(classes))


def iter_coverage_gaps(path: str | Path = CATALOG_PATH) -> Iterable[dict[str, Any]]:
    return load_catalog(path).get("remaining_coverage_gaps") or []
