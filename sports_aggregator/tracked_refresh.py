"""Run bounded refresh segments with before/after change tracking."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from sports_aggregator.scheduled_refresh import (
    REFRESH_PROFILES,
    _acquire_lock,
    _children_rss_mb,
    _refresh_statistics,
    _rss_mb,
    _run_cfbd_split,
    _run_low_memory_phase,
    _run_news_shard,
    _run_player_stats_split,
    _touch_lock,
    _write_progress,
    run_scheduled_refresh,
)

MAX_SAMPLES_PER_TABLE = 8
TRACKED_TABLES = (
    "teams", "players", "games", "game_lines", "records", "coaches",
    "rankings", "team_stats", "advanced_stats", "core_ratings",
    "content_items", "content_ingestion_runs",
)

CORE_DATASETS = ["teams", "games", "betting_lines", "media", "records", "coaches", "rankings"]
STATS_DATASETS = ["team_stats", "advanced_stats", "core_ratings"]
CONTENT_STEPS = ["articles", "bluesky", "reddit", "youtube", "podcasts", "retag", "cluster", "roles", "score"]
ROSTER_STEPS = ["cfbd-roster-context", "cfbd-recruits", "transfer-grades"]
MODEL_STEPS = ["cfbd-models", "cfbd-box-scores", "cfbd-lines", "weather"]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _hours(name: str, default: str) -> set[int]:
    return {int(value.strip()) for value in os.getenv(name, default).split(",") if value.strip()}


def _segment_for_light(now: datetime | None = None) -> str:
    zone = ZoneInfo(os.getenv("CFB_REFRESH_TIMEZONE", "America/New_York"))
    moment = (now or datetime.now(timezone.utc)).astimezone(zone)
    schedule = (
        ("core", _hours("CFB_REFRESH_CORE_HOURS", "6,18")),
        ("content", _hours("CFB_REFRESH_CONTENT_HOURS", "10,16")),
        ("rosters", _hours("CFB_REFRESH_ROSTER_HOURS", "12")),
        ("stats", _hours("CFB_REFRESH_STATS_HOURS", "22")),
        ("models", _hours("CFB_REFRESH_MODEL_HOURS", "23")),
    )
    for name, hours in schedule:
        if moment.hour in hours:
            return name
    return "core"


def _table_info(connection: sqlite3.Connection, table: str, schema: str = "main") -> list[sqlite3.Row]:
    return connection.execute(f"PRAGMA {schema}.table_info({_quote(table)})").fetchall()


def _table_exists(connection: sqlite3.Connection, table: str, schema: str = "main") -> bool:
    return connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _key_columns(info: list[sqlite3.Row]) -> list[str]:
    primary = [str(row[1]) for row in sorted(info, key=lambda row: int(row[5] or 0)) if row[5]]
    if primary:
        return primary
    names = [str(row[1]) for row in info]
    preferred = [name for name in names if name in {
        "season", "game_id", "player_id", "team_id", "school", "provider",
        "content_id", "run_id",
    }]
    return preferred or names[:1]


def _snapshot(database: Path, snapshot: Path, season: int) -> list[str]:
    """Keep the audit snapshot small and disk-backed."""
    del season
    snapshot.unlink(missing_ok=True)
    copied: list[str] = []
    content_columns = (
        "content_id,platform,platform_content_id,canonical_url,title,summary,author_name,"
        "publisher_name,published_at,ingested_at,content_type,source_role"
    )
    with sqlite3.connect(snapshot) as out:
        out.execute("ATTACH DATABASE ? AS live", (str(database),))
        for table in TRACKED_TABLES:
            if not _table_exists(out, table, "live"):
                continue
            select = content_columns if table == "content_items" else "*"
            out.execute(f"CREATE TABLE {_quote(table)} AS SELECT {select} FROM live.{_quote(table)}")
            copied.append(table)
        out.commit()
    return copied


def _key_match(alias_a: str, alias_b: str, keys: list[str]) -> str:
    return " AND ".join(f"{alias_a}.{_quote(key)} IS {alias_b}.{_quote(key)}" for key in keys)


def _row_key(row: sqlite3.Row, keys: list[str]) -> str:
    parts = [f"{key}={row[key]}" for key in keys if key in row.keys()]
    for name in ("platform", "title", "publisher_name"):
        if name in row.keys() and row[name]:
            parts.append(str(row[name]))
    return " · ".join(parts)[:220]


def _diff_table(connection: sqlite3.Connection, table: str) -> dict[str, Any] | None:
    if not _table_exists(connection, table) or not _table_exists(connection, table, "snap"):
        return None
    main_info = _table_info(connection, table)
    snap_info = _table_info(connection, table, "snap")
    main_columns = [str(row[1]) for row in main_info]
    snap_columns = {str(row[1]) for row in snap_info}
    columns = [name for name in main_columns if name in snap_columns]
    keys = [name for name in _key_columns(main_info) if name in snap_columns]
    if not columns or not keys:
        return None
    match = _key_match("n", "o", keys)
    added = connection.execute(
        f"SELECT COUNT(*) FROM main.{_quote(table)} n WHERE NOT EXISTS "
        f"(SELECT 1 FROM snap.{_quote(table)} o WHERE {match})"
    ).fetchone()[0]
    removed = connection.execute(
        f"SELECT COUNT(*) FROM snap.{_quote(table)} o WHERE NOT EXISTS "
        f"(SELECT 1 FROM main.{_quote(table)} n WHERE {match})"
    ).fetchone()[0]
    nonkeys = [column for column in columns if column not in keys]
    changed_expr = " OR ".join(f"n.{_quote(column)} IS NOT o.{_quote(column)}" for column in nonkeys) or "0"
    changed = connection.execute(
        f"SELECT COUNT(*) FROM main.{_quote(table)} n JOIN snap.{_quote(table)} o ON {match} WHERE {changed_expr}"
    ).fetchone()[0]
    connection.row_factory = sqlite3.Row
    samples: list[dict[str, Any]] = []
    if added:
        rows = connection.execute(
            f"SELECT n.* FROM main.{_quote(table)} n WHERE NOT EXISTS "
            f"(SELECT 1 FROM snap.{_quote(table)} o WHERE {match}) LIMIT ?", (MAX_SAMPLES_PER_TABLE,)
        ).fetchall()
        samples.extend({"kind": "added", "key": _row_key(row, keys)} for row in rows)
    if removed and len(samples) < MAX_SAMPLES_PER_TABLE:
        rows = connection.execute(
            f"SELECT o.* FROM snap.{_quote(table)} o WHERE NOT EXISTS "
            f"(SELECT 1 FROM main.{_quote(table)} n WHERE {match}) LIMIT ?",
            (MAX_SAMPLES_PER_TABLE - len(samples),)
        ).fetchall()
        samples.extend({"kind": "removed", "key": _row_key(row, keys)} for row in rows)
    if changed and len(samples) < MAX_SAMPLES_PER_TABLE:
        rows = connection.execute(
            f"SELECT n.* FROM main.{_quote(table)} n JOIN snap.{_quote(table)} o ON {match} "
            f"WHERE {changed_expr} LIMIT ?", (MAX_SAMPLES_PER_TABLE - len(samples),)
        ).fetchall()
        for new_row in rows:
            old_where = " AND ".join(f"{_quote(key)} IS ?" for key in keys)
            old = connection.execute(
                f"SELECT * FROM snap.{_quote(table)} WHERE {old_where} LIMIT 1",
                [new_row[key] for key in keys],
            ).fetchone()
            fields = []
            if old is not None:
                for column in nonkeys:
                    if new_row[column] != old[column]:
                        fields.append({"field": column, "before": str(old[column])[:80], "after": str(new_row[column])[:80]})
                        if len(fields) >= 4:
                            break
            samples.append({"kind": "changed", "key": _row_key(new_row, keys), "fields": fields})
    return {"table": table, "added": int(added), "changed": int(changed), "removed": int(removed), "samples": samples}


def _write_ledger(instance: Path, report: dict[str, Any], database: Path, snapshot: Path,
                  tables: list[str], tracking_error: str | None = None) -> None:
    changes: list[dict[str, Any]] = []
    if database.exists() and snapshot.exists():
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("ATTACH DATABASE ? AS snap", (str(snapshot),))
            for table in tables:
                try:
                    result = _diff_table(connection, table)
                except sqlite3.Error:
                    result = None
                if result and (result["added"] or result["changed"] or result["removed"]):
                    changes.append(result)
    ledger = {
        "profile": report.get("profile"), "season": report.get("season"), "status": report.get("status"),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at") or datetime.now(timezone.utc).isoformat(),
        "tracking_error": (tracking_error or "")[:240], "changes": changes,
        "totals": {
            "added": sum(item["added"] for item in changes),
            "changed": sum(item["changed"] for item in changes),
            "removed": sum(item["removed"] for item in changes),
        },
    }
    with (instance / "refresh_change_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger, separators=(",", ":")) + "\n")


def _segment_results(segment: str, season: int, *, root: Path, log, heartbeat) -> list[dict[str, Any]]:
    if segment == "core":
        results = [_run_cfbd_split(season, root=root, timeout=600, log=log, datasets=CORE_DATASETS, heartbeat=heartbeat)]
        results += _run_low_memory_phase("refresh", season, root=root, only=["weather"], timeout=600, log=log, heartbeat=heartbeat)
        return results
    if segment == "rosters":
        results = [_run_cfbd_split(season, root=root, timeout=300, log=log, datasets=["players"], heartbeat=heartbeat)]
        results += _run_low_memory_phase("refresh", season, root=root, only=ROSTER_STEPS, timeout=600, log=log, heartbeat=heartbeat)
        return results
    if segment == "stats":
        results = [_run_cfbd_split(season, root=root, timeout=600, log=log, datasets=STATS_DATASETS, heartbeat=heartbeat)]
        results.append(_run_player_stats_split(season, root=root, timeout=300, log=log, optional=True, heartbeat=heartbeat))
        return results
    if segment == "models":
        return _run_low_memory_phase("refresh", season, root=root, only=MODEL_STEPS, timeout=600, log=log, heartbeat=heartbeat)
    if segment == "content":
        return _run_low_memory_phase("refresh", season, root=root, only=CONTENT_STEPS, timeout=600, log=log, heartbeat=heartbeat)
    if segment == "news":
        return [_run_news_shard(season, timeout=600, log=log)]
    raise ValueError(f"unknown refresh segment: {segment}")


def _run_segment(segment: str, season: int, *, root: Path, instance: Path) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    lock = instance / "scheduled_refresh.lock"
    if not _acquire_lock(lock, started, 1):
        return {"status": "skipped", "reason": "refresh_already_running", "profile": segment, "season": season}
    logs = instance / "refresh_logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"refresh-{started.strftime('%Y%m%dT%H%M%SZ')}.log"
    progress = {"season": season, "profile": segment, "started_at": started.isoformat(), "completed": False, "steps": {}}
    _write_progress(instance, progress)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            print(f"segmented refresh: profile={segment} season={season} parent_rss_mb={_rss_mb()}", file=log, flush=True)
            results = _segment_results(segment, season, root=root, log=log, heartbeat=lambda: _touch_lock(lock))
            for result in results:
                progress["steps"][str(result.get("step"))] = {
                    "status": str(result.get("status")), "at": datetime.now(timezone.utc).isoformat(),
                    "message": str(result.get("message") or "")[:180],
                }
                _write_progress(instance, progress)
        _refresh_statistics(instance)
        finished = datetime.now(timezone.utc)
        required = [row for row in results if row.get("status") not in {"success", "skipped"} and not row.get("optional", False)]
        degraded = [row for row in results if row.get("status") not in {"success", "skipped"} and row.get("optional", False)]
        status = "failed" if required else "degraded" if degraded else "success"
        report = {
            "status": status, "profile": segment, "season": season,
            "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "seconds": round((finished - started).total_seconds(), 1), "exit_code": 1 if required else 0,
            "log": str(log_path), "step_count": len(results),
            "degraded_steps": [{"step": str(r.get("step")), "status": str(r.get("status")), "message": str(r.get("message") or "")[:240]} for r in degraded],
            "degraded_count": len(degraded), "required_failure_count": len(required),
            "parent_peak_rss_mb": _rss_mb(), "child_peak_rss_mb": _children_rss_mb(), "resumed_steps": [],
        }
        progress["completed"] = True
        progress["finished_at"] = finished.isoformat()
        _write_progress(instance, progress)
        with (instance / "scheduled_refresh_history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, separators=(",", ":")) + "\n")
        return report
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description="Run a bounded scheduled refresh with change tracking")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--profile", choices=tuple(sorted(REFRESH_PROFILES)), default="light")
    args = parser.parse_args(argv)
    database = Path((os.getenv("CFB_DATABASE_PATH") or "").strip() or root / "instance" / "cfb.sqlite3")
    if not database.is_absolute():
        database = root / database
    instance = database.parent
    snapshot = instance / ".refresh_before.sqlite3"
    tables: list[str] = []
    tracking_error: str | None = None
    try:
        if database.exists():
            try:
                tables = _snapshot(database, snapshot, args.season)
            except Exception as exc:
                tracking_error = f"snapshot failed: {type(exc).__name__}: {exc}"
                snapshot.unlink(missing_ok=True)

        if args.profile in {"scores", "results"}:
            report = run_scheduled_refresh(args.season, profile=args.profile, repo_root=root)
        else:
            segment = "news" if args.profile == "news" else _segment_for_light()
            if args.profile == "heavy":
                segment = "core"
            report = _run_segment(segment, args.season, root=root, instance=instance)

        if report.get("status") != "skipped":
            try:
                _write_ledger(instance, report, database, snapshot, tables, tracking_error)
            except Exception as exc:
                try:
                    _write_ledger(instance, report, database, Path("__missing__"), [], f"ledger failed: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        print(json.dumps(report, sort_keys=True))
        return 0 if report.get("status") == "skipped" else int(report.get("exit_code", 1))
    finally:
        snapshot.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
