"""Run a scheduled refresh with a low-memory before/after change ledger."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from dotenv import load_dotenv

from sports_aggregator.scheduled_refresh import REFRESH_PROFILES, run_scheduled_refresh

MAX_SAMPLES_PER_TABLE = 8
TRACKED_TABLES = (
    "teams", "players", "games", "game_lines", "records", "coaches",
    "rankings", "team_stats", "advanced_stats", "core_ratings",
    "content_items", "content_ingestion_runs",
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_info(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
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
    """Copy modest tracked tables; huge historical detail tables stay excluded."""
    del season
    snapshot.unlink(missing_ok=True)
    copied: list[str] = []
    with sqlite3.connect(snapshot) as out:
        out.execute("ATTACH DATABASE ? AS live", (str(database),))
        for table in TRACKED_TABLES:
            exists = out.execute(
                "SELECT 1 FROM live.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            out.execute(
                f"CREATE TABLE {_quote(table)} AS SELECT * FROM live.{_quote(table)}"
            )
            copied.append(table)
        out.commit()
    return copied


def _key_match(alias_a: str, alias_b: str, keys: list[str]) -> str:
    return " AND ".join(
        f"{alias_a}.{_quote(key)} IS {alias_b}.{_quote(key)}" for key in keys
    )


def _row_key(row: sqlite3.Row, keys: list[str]) -> str:
    parts = [f"{key}={row[key]}" for key in keys if key in row.keys()]
    if "platform" in row.keys() and row["platform"]:
        parts.append(str(row["platform"]))
    if "title" in row.keys() and row["title"]:
        parts.append(str(row["title"]))
    if "publisher_name" in row.keys() and row["publisher_name"]:
        parts.append(str(row["publisher_name"]))
    return " · ".join(parts)[:220]


def _diff_table(connection: sqlite3.Connection, table: str) -> dict[str, Any] | None:
    if not _table_exists(connection, table):
        return None
    snap_exists = connection.execute(
        "SELECT 1 FROM snap.sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not snap_exists:
        return None
    info = _table_info(connection, table)
    columns = [str(row[1]) for row in info]
    keys = _key_columns(info)
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
    changed_expr = " OR ".join(
        f"n.{_quote(column)} IS NOT o.{_quote(column)}" for column in nonkeys
    ) or "0"
    changed = connection.execute(
        f"SELECT COUNT(*) FROM main.{_quote(table)} n JOIN snap.{_quote(table)} o ON {match} "
        f"WHERE {changed_expr}"
    ).fetchone()[0]
    samples: list[dict[str, Any]] = []
    connection.row_factory = sqlite3.Row
    if added:
        rows = connection.execute(
            f"SELECT n.* FROM main.{_quote(table)} n WHERE NOT EXISTS "
            f"(SELECT 1 FROM snap.{_quote(table)} o WHERE {match}) LIMIT ?",
            (MAX_SAMPLES_PER_TABLE,),
        ).fetchall()
        samples.extend({"kind": "added", "key": _row_key(row, keys)} for row in rows)
    if removed and len(samples) < MAX_SAMPLES_PER_TABLE:
        rows = connection.execute(
            f"SELECT o.* FROM snap.{_quote(table)} o WHERE NOT EXISTS "
            f"(SELECT 1 FROM main.{_quote(table)} n WHERE {match}) LIMIT ?",
            (MAX_SAMPLES_PER_TABLE - len(samples),),
        ).fetchall()
        samples.extend({"kind": "removed", "key": _row_key(row, keys)} for row in rows)
    if changed and len(samples) < MAX_SAMPLES_PER_TABLE:
        limit = MAX_SAMPLES_PER_TABLE - len(samples)
        rows = connection.execute(
            f"SELECT n.* FROM main.{_quote(table)} n "
            f"JOIN snap.{_quote(table)} o ON {match} WHERE {changed_expr} LIMIT ?", (limit,)
        ).fetchall()
        for new_row in rows:
            key_values = [new_row[key] for key in keys]
            old_where = " AND ".join(f"{_quote(key)} IS ?" for key in keys)
            old = connection.execute(
                f"SELECT * FROM snap.{_quote(table)} WHERE {old_where} LIMIT 1", key_values
            ).fetchone()
            changed_fields = []
            if old is not None:
                for column in nonkeys:
                    if new_row[column] != old[column]:
                        if column in {"raw_json", "body_text", "evidence_json", "factors_json", "errors_json"}:
                            continue
                        changed_fields.append({
                            "field": column,
                            "before": str(old[column])[:80],
                            "after": str(new_row[column])[:80],
                        })
                        if len(changed_fields) >= 4:
                            break
            samples.append({"kind": "changed", "key": _row_key(new_row, keys), "fields": changed_fields})
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
        "profile": report.get("profile"),
        "season": report.get("season"),
        "status": report.get("status"),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at") or datetime.now(timezone.utc).isoformat(),
        "tracking_error": (tracking_error or "")[:240],
        "changes": changes,
        "totals": {
            "added": sum(item["added"] for item in changes),
            "changed": sum(item["changed"] for item in changes),
            "removed": sum(item["removed"] for item in changes),
        },
    }
    with (instance / "refresh_change_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description="Run scheduled refresh with change tracking")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--profile", choices=tuple(sorted(REFRESH_PROFILES)), default="heavy")
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
                tables = []
                snapshot.unlink(missing_ok=True)

        # The underlying refresh is authoritative. Change tracking must never stop it.
        report = run_scheduled_refresh(args.season, profile=args.profile, repo_root=root)

        if report.get("status") != "skipped":
            try:
                _write_ledger(instance, report, database, snapshot, tables, tracking_error)
            except Exception as exc:
                # Preserve the successful/failed refresh report even when audit tracking fails.
                tracking_error = f"ledger failed: {type(exc).__name__}: {exc}"
                try:
                    _write_ledger(instance, report, database, Path("__missing__"), [], tracking_error)
                except Exception:
                    pass
        print(json.dumps(report, sort_keys=True))
        return 0 if report.get("status") == "skipped" else int(report.get("exit_code", 1))
    finally:
        snapshot.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
