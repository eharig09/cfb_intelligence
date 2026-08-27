"""Sanitized refresh-status page for the college-football app.

This module deliberately exposes only reader-safe refresh metadata. Raw logs,
filesystem paths, process IDs, secrets, and exception traces remain available
only through the authenticated internal status endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, render_template


data_status_pages = Blueprint("cfb_data_status", __name__)

_STEP_LABELS = {
    "cfbd-sync": "Core CFBD data",
    "cfbd-lines": "Betting lines",
    "cfbd-box-scores": "Box scores",
    "cfbd-current-player-stats": "Player statistics",
    "articles": "Articles",
    "local-articles": "Local reporting",
    "local-news-shard": "Local reporting",
    "weather": "Weather",
    "bluesky": "Bluesky",
    "reddit": "Reddit",
    "youtube": "YouTube",
    "podcasts": "Podcasts",
}


def _instance_dir() -> Path:
    return Path(current_app.config["CFB_DATABASE_PATH"]).parent


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _read_history(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows[-limit:][::-1]


def _local_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(current_app.config.get("CFB_DISPLAY_TIMEZONE", "America/New_York"))
    return parsed.astimezone(zone)


def _display_time(value: Any) -> str:
    parsed = _local_datetime(value)
    if parsed is None:
        return "Not recorded"
    return parsed.strftime("%b %-d, %Y · %-I:%M %p %Z") if parsed.strftime("%d") else parsed.isoformat()


def _relative_time(value: Any) -> str:
    parsed = _local_datetime(value)
    if parsed is None:
        return "unknown"
    seconds = max(0, int((datetime.now(parsed.tzinfo) - parsed).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 36:
        return f"{hours} hr ago"
    days = hours // 24
    return f"{days} d ago"


def _safe_step(row: dict[str, Any]) -> dict[str, Any]:
    step = str(row.get("step") or "unknown")
    result = {
        "step": step,
        "label": _STEP_LABELS.get(step, step.replace("-", " ").title()),
        "status": str(row.get("status") or "unknown"),
        "at": row.get("at") or row.get("finished_at"),
        "message": str(row.get("message") or "")[:180],
    }
    for key in ("added", "updated", "unchanged", "count"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            result[key] = int(value)
    return result


def _status_model() -> dict[str, Any]:
    instance = _instance_dir()
    progress = _read_json(instance / "refresh_progress.json")
    history = _read_history(instance / "scheduled_refresh_history.jsonl")
    running = (instance / "scheduled_refresh.lock").exists()

    latest = history[0] if history else {}
    latest_steps = latest.get("steps") if isinstance(latest.get("steps"), list) else []
    if not latest_steps:
        latest_steps = [
            {"step": name, **(entry if isinstance(entry, dict) else {})}
            for name, entry in (progress.get("steps") or {}).items()
        ]

    sections = [_safe_step(row) for row in latest_steps if isinstance(row, dict)]
    for row in sections:
        row["time_label"] = _display_time(row.get("at"))
        row["relative_label"] = _relative_time(row.get("at"))

    recent_runs: list[dict[str, Any]] = []
    for item in history:
        recent_runs.append({
            "profile": str(item.get("profile") or "unknown"),
            "season": item.get("season"),
            "status": str(item.get("status") or "unknown"),
            "started_label": _display_time(item.get("started_at")),
            "finished_label": _display_time(item.get("finished_at")),
            "seconds": item.get("seconds"),
            "step_count": item.get("step_count"),
            "degraded_count": item.get("degraded_count", 0),
            "required_failure_count": item.get("required_failure_count", 0),
        })

    latest_finished = latest.get("finished_at") or progress.get("finished_at")
    return {
        "running": running,
        "latest_status": str(latest.get("status") or ("running" if running else "unknown")),
        "latest_profile": str(latest.get("profile") or progress.get("profile") or "unknown"),
        "latest_finished_label": _display_time(latest_finished),
        "latest_relative_label": _relative_time(latest_finished),
        "sections": sections,
        "recent_runs": recent_runs,
    }


@data_status_pages.app_context_processor
def inject_data_freshness() -> dict[str, Any]:
    """Expose a tiny freshness packet to the shared layout."""
    model = _status_model()
    return {
        "data_freshness": {
            "running": model["running"],
            "status": model["latest_status"],
            "relative": model["latest_relative_label"],
        }
    }


@data_status_pages.get("/college-football/data-status/")
def data_status():
    return render_template("cfb_data_status.html", status=_status_model())
