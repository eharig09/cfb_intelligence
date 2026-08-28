"""Sanitized refresh status and resilient content-linkage audit for the CFB app."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, jsonify, render_template, request


data_status_pages = Blueprint("cfb_data_status", __name__)

_STEP_LABELS = {
    "cfbd-sync": "Core CFBD data", "cfbd-lines": "Betting lines",
    "cfbd-box-scores": "Box scores", "cfbd-current-player-stats": "Player statistics",
    "articles": "Articles", "local-articles": "Local reporting",
    "local-news-shard": "Local reporting", "weather": "Weather",
    "bluesky": "Bluesky", "reddit": "Reddit", "youtube": "YouTube",
    "podcasts": "Podcasts",
}

_TABLE_LABELS = {
    "teams": "Teams", "players": "Rosters", "games": "Games & schedules",
    "game_lines": "Betting lines", "records": "Team records", "coaches": "Coaches",
    "rankings": "Rankings", "team_stats": "Team statistics",
    "advanced_stats": "Advanced statistics", "core_ratings": "CORE ratings",
    "content_items": "News & social content", "content_ingestion_runs": "Content ingestion runs",
}


def _database_path() -> Path:
    return Path(current_app.config["CFB_DATABASE_PATH"])


def _instance_dir() -> Path:
    return _database_path().parent


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_history(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows: deque[dict[str, Any]] = deque(maxlen=limit)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return list(rows)[::-1]
    except Exception:
        return []


def _local_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        zone = ZoneInfo(current_app.config.get("CFB_DISPLAY_TIMEZONE", "America/New_York"))
        return parsed.astimezone(zone)
    except Exception:
        return None


def _display_time(value: Any) -> str:
    parsed = _local_datetime(value)
    return parsed.strftime("%b %-d, %Y · %-I:%M %p %Z") if parsed else "Not recorded"


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
    return f"{hours // 24} d ago"


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


def _safe_change_ledger(instance: Path) -> dict[str, Any] | None:
    try:
        rows = _read_history(instance / "refresh_change_history.jsonl", limit=1)
        if not rows:
            return None
        raw = rows[0]
        tables: list[dict[str, Any]] = []
        for item in raw.get("changes") or []:
            if not isinstance(item, dict):
                continue
            samples = []
            for sample in (item.get("samples") or [])[:8]:
                if not isinstance(sample, dict):
                    continue
                fields = []
                for field in (sample.get("fields") or [])[:4]:
                    if isinstance(field, dict):
                        fields.append({
                            "field": str(field.get("field") or "")[:60],
                            "before": str(field.get("before") or "")[:80],
                            "after": str(field.get("after") or "")[:80],
                        })
                samples.append({
                    "kind": str(sample.get("kind") or "changed")[:20],
                    "key": str(sample.get("key") or "")[:220],
                    "fields": fields,
                })
            table = str(item.get("table") or "unknown")
            tables.append({
                "table": table,
                "label": _TABLE_LABELS.get(table, table.replace("_", " ").title()),
                "added": int(item.get("added") or 0),
                "changed": int(item.get("changed") or 0),
                "removed": int(item.get("removed") or 0),
                "samples": samples,
            })
        totals = raw.get("totals") or {}
        return {
            "finished_label": _display_time(raw.get("finished_at")),
            "relative_label": _relative_time(raw.get("finished_at")),
            "profile": str(raw.get("profile") or "unknown"),
            "added": int(totals.get("added") or 0),
            "changed": int(totals.get("changed") or 0),
            "removed": int(totals.get("removed") or 0),
            "tracking_error": str(raw.get("tracking_error") or "")[:240],
            "tables": tables,
        }
    except Exception:
        return None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    try:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except sqlite3.Error:
        return set()


def _safe_url(value: Any) -> str:
    try:
        text = str(value or "").strip()
        parsed = urlparse(text)
        return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    except Exception:
        return ""


def _audit_empty(error: str = "", *, platform: str = "", sample_mode: str = "random",
                 limit: int = 80, connection_limit: int = 100) -> dict[str, Any]:
    return {
        "items": [], "connections": [], "flagged": [], "platforms": [],
        "available": False, "error": str(error or "")[:180],
        "selected_platform": platform, "sample_mode": sample_mode,
        "limit": limit, "connection_limit": connection_limit,
    }


def _audit_model(*, limit: int = 80, platform: str = "", sample_mode: str = "random",
                 connection_limit: int = 100) -> dict[str, Any]:
    limit = max(10, min(int(limit), 200))
    connection_limit = max(25, min(int(connection_limit), 250))
    sample_mode = sample_mode if sample_mode in {"random", "recent"} else "random"
    platform = str(platform or "").strip().casefold()[:40]
    database = _database_path()
    if not database.exists():
        return _audit_empty("database not found", platform=platform, sample_mode=sample_mode,
                            limit=limit, connection_limit=connection_limit)
    try:
        with sqlite3.connect(database, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            if not all(_table_exists(connection, table) for table in ("content_items", "content_teams", "teams")):
                return _audit_empty("content linkage tables are not available", platform=platform,
                                    sample_mode=sample_mode, limit=limit,
                                    connection_limit=connection_limit)

            ci_cols = _columns(connection, "content_items")
            ct_cols = _columns(connection, "content_teams")
            team_cols = _columns(connection, "teams")
            if not {"content_id", "platform", "title", "ingested_at"}.issubset(ci_cols):
                return _audit_empty("content linkage schema is incomplete", platform=platform,
                                    sample_mode=sample_mode, limit=limit,
                                    connection_limit=connection_limit)
            if not {"content_id", "team_id", "confidence", "method"}.issubset(ct_cols):
                return _audit_empty("content linkage schema is incomplete", platform=platform,
                                    sample_mode=sample_mode, limit=limit,
                                    connection_limit=connection_limit)
            if not {"team_id", "school"}.issubset(team_cols):
                return _audit_empty("content linkage schema is incomplete", platform=platform,
                                    sample_mode=sample_mode, limit=limit,
                                    connection_limit=connection_limit)

            def ci(name: str, fallback: str = "NULL") -> str:
                return f'ci."{name}"' if name in ci_cols else fallback

            platforms = [str(row[0]) for row in connection.execute(
                "SELECT DISTINCT platform FROM content_items WHERE platform IS NOT NULL AND platform <> '' ORDER BY platform"
            ).fetchall()]
            if platform and platform not in {value.casefold() for value in platforms}:
                platform = ""

            feedback = _table_exists(connection, "content_team_feedback")
            feedback_join = (
                "LEFT JOIN content_team_feedback f ON f.content_id=ci.content_id AND f.team_id=ct.team_id"
                if feedback else ""
            )
            feedback_select = (
                "f.verdict AS feedback_verdict, f.reason AS feedback_reason"
                if feedback else "NULL AS feedback_verdict, NULL AS feedback_reason"
            )
            where_parts = ["1=1"]
            params: list[Any] = []
            if platform:
                where_parts.append("LOWER(ci.platform)=?")
                params.append(platform)
            if feedback:
                where_parts.append(
                    "NOT EXISTS (SELECT 1 FROM content_team_feedback fx WHERE fx.content_id=ci.content_id AND fx.team_id=ct.team_id AND fx.verdict='bad')"
                )
            where_sql = " AND ".join(where_parts)
            published_expr = ci("published_at", "ci.ingested_at")
            order_sql = "RANDOM()" if sample_mode == "random" else f"COALESCE({published_expr}, ci.ingested_at) DESC, ci.content_id DESC"

            rows = connection.execute(
                f"""
                SELECT ci.content_id, ci.platform, ci.title,
                       {ci('canonical_url')} AS canonical_url,
                       {ci('original_url')} AS original_url,
                       {ci('publisher_name')} AS publisher_name,
                       {ci('author_name')} AS author_name,
                       {published_expr} AS published_at,
                       ci.ingested_at,
                       {ci('content_type')} AS content_type,
                       {ci('source_role')} AS source_role,
                       ct.team_id, ct.confidence, ct.method, t.school,
                       {feedback_select}
                  FROM content_items ci
                  JOIN content_teams ct ON ct.content_id=ci.content_id
                  JOIN teams t ON t.team_id=ct.team_id
                  {feedback_join}
                 WHERE {where_sql}
                 ORDER BY {order_sql}
                 LIMIT ?
                """, (*params, limit)
            ).fetchall()

            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    published_raw = row["published_at"]
                    source = str(row["publisher_name"] or row["author_name"] or row["platform"] or "Unknown")
                    items.append({
                        "content_id": int(row["content_id"]),
                        "team_id": int(row["team_id"]),
                        "team": str(row["school"] or "Unknown")[:100],
                        "platform": str(row["platform"] or "unknown")[:30],
                        "source": source[:120],
                        "title": str(row["title"] or "Untitled")[:240],
                        "url": _safe_url(row["canonical_url"] or row["original_url"]),
                        "published_label": _display_time(published_raw),
                        "published_age": _relative_time(published_raw),
                        "ingested_label": _display_time(row["ingested_at"]),
                        "ingested_age": _relative_time(row["ingested_at"]),
                        "content_type": str(row["content_type"] or "")[:60],
                        "source_role": str(row["source_role"] or "")[:60],
                        "confidence": round(float(row["confidence"] or 0), 3),
                        "method": str(row["method"] or "unknown")[:120],
                        "feedback_verdict": str(row["feedback_verdict"] or ""),
                        "feedback_reason": str(row["feedback_reason"] or "")[:180],
                    })
                except Exception:
                    continue

            source_expr = (
                "COALESCE(NULLIF(ci.publisher_name,''), NULLIF(ci.author_name,''), ci.platform)"
                if {"publisher_name", "author_name"}.issubset(ci_cols) else "ci.platform"
            )
            conn_where = ["1=1"]
            conn_params: list[Any] = []
            if platform:
                conn_where.append("LOWER(ci.platform)=?")
                conn_params.append(platform)
            if feedback:
                conn_where.append(
                    "NOT EXISTS (SELECT 1 FROM content_team_feedback fx WHERE fx.content_id=ci.content_id AND fx.team_id=ct.team_id AND fx.verdict='bad')"
                )
            connections = [dict(row) for row in connection.execute(
                f"""
                SELECT {source_expr} AS source, ci.platform, t.school AS team,
                       COUNT(*) AS item_count, ROUND(AVG(ct.confidence), 3) AS avg_confidence,
                       MAX({published_expr}) AS newest_published,
                       MAX(ci.ingested_at) AS last_ingested
                  FROM content_items ci
                  JOIN content_teams ct ON ct.content_id=ci.content_id
                  JOIN teams t ON t.team_id=ct.team_id
                 WHERE {' AND '.join(conn_where)}
                 GROUP BY source, ci.platform, t.team_id, t.school
                 ORDER BY item_count DESC, newest_published DESC
                 LIMIT ?
                """, (*conn_params, connection_limit)
            ).fetchall()]
            for row in connections:
                row["source"] = str(row.get("source") or "Unknown")[:120]
                row["platform"] = str(row.get("platform") or "unknown")[:30]
                row["team"] = str(row.get("team") or "Unknown")[:100]
                newest = row.pop("newest_published", None)
                ingested = row.pop("last_ingested", None)
                row["newest_label"] = _display_time(newest)
                row["newest_age"] = _relative_time(newest)
                row["last_ingested_label"] = _display_time(ingested)

            flagged: list[dict[str, Any]] = []
            if feedback:
                flagged_rows = connection.execute(
                    f"""
                    SELECT f.content_id, f.team_id, f.reason, f.created_at,
                           ci.title, ci.platform,
                           {ci('canonical_url')} AS canonical_url,
                           {ci('original_url')} AS original_url,
                           {source_expr} AS source, t.school AS team
                      FROM content_team_feedback f
                      JOIN content_items ci ON ci.content_id=f.content_id
                      JOIN teams t ON t.team_id=f.team_id
                     WHERE f.verdict='bad'
                     ORDER BY f.created_at DESC
                     LIMIT 50
                    """
                ).fetchall()
                for raw in flagged_rows:
                    try:
                        row = dict(raw)
                        row["url"] = _safe_url(row.pop("canonical_url", "") or row.pop("original_url", ""))
                        row["created_label"] = _display_time(row.pop("created_at", None))
                        row["title"] = str(row.get("title") or "Untitled")[:240]
                        row["reason"] = str(row.get("reason") or "")[:180]
                        row["source"] = str(row.get("source") or "Unknown")[:120]
                        row["team"] = str(row.get("team") or "Unknown")[:100]
                        flagged.append(row)
                    except Exception:
                        continue

            return {
                "items": items, "connections": connections, "flagged": flagged,
                "platforms": platforms, "available": True, "error": "",
                "selected_platform": platform, "sample_mode": sample_mode,
                "limit": limit, "connection_limit": connection_limit,
            }
    except Exception as exc:
        return _audit_empty(f"audit unavailable: {type(exc).__name__}", platform=platform,
                            sample_mode=sample_mode, limit=limit,
                            connection_limit=connection_limit)


def _ensure_feedback_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS content_team_feedback (
            content_id INTEGER NOT NULL, team_id INTEGER NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('bad')),
            reason TEXT NOT NULL DEFAULT '', previous_confidence REAL,
            previous_method TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY(content_id, team_id)
        );
        CREATE INDEX IF NOT EXISTS idx_content_team_feedback_verdict
            ON content_team_feedback(verdict, created_at DESC);
        CREATE TRIGGER IF NOT EXISTS content_team_feedback_block_bad
        BEFORE INSERT ON content_teams
        WHEN EXISTS (
            SELECT 1 FROM content_team_feedback f
            WHERE f.content_id=NEW.content_id AND f.team_id=NEW.team_id AND f.verdict='bad'
        )
        BEGIN SELECT RAISE(IGNORE); END;
        """
    )


def _require_audit_auth() -> None:
    expected = str(current_app.config.get("CFB_REFRESH_TOKEN") or "").strip()
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not expected:
        abort(503, description="CFB_REFRESH_TOKEN is not configured")
    if not provided or not secrets.compare_digest(provided, expected):
        abort(401)


def _status_model(include_audit: bool = True, *, audit_options: dict[str, Any] | None = None) -> dict[str, Any]:
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
    recent_runs = [{
        "profile": str(item.get("profile") or "unknown"),
        "season": item.get("season"), "status": str(item.get("status") or "unknown"),
        "started_label": _display_time(item.get("started_at")),
        "finished_label": _display_time(item.get("finished_at")),
        "seconds": item.get("seconds"), "step_count": item.get("step_count"),
        "degraded_count": item.get("degraded_count", 0),
        "required_failure_count": item.get("required_failure_count", 0),
    } for item in history]
    latest_finished = latest.get("finished_at") or progress.get("finished_at")
    options = audit_options or {}
    return {
        "running": running,
        "latest_status": str(latest.get("status") or ("running" if running else "unknown")),
        "latest_profile": str(latest.get("profile") or progress.get("profile") or "unknown"),
        "latest_finished_label": _display_time(latest_finished),
        "latest_relative_label": _relative_time(latest_finished),
        "sections": sections, "recent_runs": recent_runs,
        "change_ledger": _safe_change_ledger(instance),
        "audit": _audit_model(**options) if include_audit else _audit_empty(),
    }


@data_status_pages.app_context_processor
def inject_data_freshness() -> dict[str, Any]:
    model = _status_model(include_audit=False)
    return {"data_freshness": {
        "running": model["running"], "status": model["latest_status"],
        "relative": model["latest_relative_label"],
    }}


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@data_status_pages.get("/college-football/data-status/")
def data_status():
    options = {
        "platform": request.args.get("platform", ""),
        "sample_mode": request.args.get("sample", "random"),
        "limit": _int_arg("limit", 80),
        "connection_limit": _int_arg("connections", 100),
    }
    try:
        model = _status_model(include_audit=True, audit_options=options)
    except Exception:
        model = _status_model(include_audit=False)
    return render_template("cfb_data_status.html", status=model)


@data_status_pages.post("/college-football/data-status/team-link-feedback")
def team_link_feedback():
    _require_audit_auth()
    payload = request.get_json(silent=True) or {}
    try:
        content_id = int(payload.get("content_id")); team_id = int(payload.get("team_id"))
    except (TypeError, ValueError):
        abort(400, description="content_id and team_id are required")
    action = str(payload.get("action") or "bad").strip().casefold()
    reason = str(payload.get("reason") or "Not relevant to this team").strip()[:180]
    if action not in {"bad", "undo"}:
        abort(400, description="action must be bad or undo")

    with sqlite3.connect(_database_path()) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_feedback_schema(connection)
        if action == "bad":
            association = connection.execute(
                "SELECT confidence, method FROM content_teams WHERE content_id=? AND team_id=?",
                (content_id, team_id),
            ).fetchone()
            if association is None:
                abort(404, description="content/team association was not found")
            connection.execute(
                """
                INSERT INTO content_team_feedback
                    (content_id, team_id, verdict, reason, previous_confidence, previous_method, created_at)
                VALUES (?, ?, 'bad', ?, ?, ?, ?)
                ON CONFLICT(content_id, team_id) DO UPDATE SET
                    verdict='bad', reason=excluded.reason,
                    previous_confidence=excluded.previous_confidence,
                    previous_method=excluded.previous_method,
                    created_at=excluded.created_at
                """,
                (content_id, team_id, reason, float(association["confidence"] or 0),
                 str(association["method"] or "manual_restore"), datetime.now(timezone.utc).isoformat()),
            )
            connection.execute("DELETE FROM content_teams WHERE content_id=? AND team_id=?", (content_id, team_id))
        else:
            previous = connection.execute(
                "SELECT previous_confidence, previous_method FROM content_team_feedback WHERE content_id=? AND team_id=? AND verdict='bad'",
                (content_id, team_id),
            ).fetchone()
            if previous is None:
                abort(404, description="bad-link feedback was not found")
            connection.execute("DELETE FROM content_team_feedback WHERE content_id=? AND team_id=?", (content_id, team_id))
            connection.execute(
                "INSERT OR IGNORE INTO content_teams(content_id, team_id, confidence, method) VALUES (?, ?, ?, ?)",
                (content_id, team_id, float(previous["previous_confidence"] or 0),
                 str(previous["previous_method"] or "manual_restore")),
            )
        connection.commit()
    return jsonify({"status": "ok", "action": action, "content_id": content_id, "team_id": team_id})
