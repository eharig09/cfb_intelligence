"""Run bounded production refresh segments without copying the live database.

This module used to snapshot and diff a large set of SQLite tables before and
after every automatic refresh. That audit was useful while building the
pipeline, but on the constrained Render web service it duplicated disk I/O and
CPU around even tiny score refreshes. Production refreshes now keep only the
bounded execution, progress/history logging, lock safety, and RSS telemetry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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

CORE_DATASETS = ["teams", "games", "betting_lines", "media", "records", "coaches", "rankings"]
STATS_DATASETS = ["team_stats", "advanced_stats", "core_ratings"]
CONTENT_STEPS = ["articles", "bluesky", "reddit", "youtube", "podcasts", "retag", "cluster", "roles", "score"]
ROSTER_STEPS = ["cfbd-roster-context", "cfbd-recruits", "transfer-grades"]
MODEL_STEPS = ["cfbd-models", "cfbd-box-scores", "cfbd-lines", "weather"]


def _hours(name: str, default: str) -> set[int]:
    return {
        int(value.strip())
        for value in os.getenv(name, default).split(",")
        if value.strip()
    }


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


def _segment_results(segment: str, season: int, *, root: Path, log, heartbeat) -> list[dict]:
    if segment == "core":
        results = [
            _run_cfbd_split(
                season,
                root=root,
                timeout=600,
                log=log,
                datasets=CORE_DATASETS,
                heartbeat=heartbeat,
            )
        ]
        results += _run_low_memory_phase(
            "refresh",
            season,
            root=root,
            only=["weather"],
            timeout=600,
            log=log,
            heartbeat=heartbeat,
        )
        return results

    if segment == "rosters":
        results = [
            _run_cfbd_split(
                season,
                root=root,
                timeout=300,
                log=log,
                datasets=["players"],
                heartbeat=heartbeat,
            )
        ]
        results += _run_low_memory_phase(
            "refresh",
            season,
            root=root,
            only=ROSTER_STEPS,
            timeout=600,
            log=log,
            heartbeat=heartbeat,
        )
        return results

    if segment == "stats":
        results = [
            _run_cfbd_split(
                season,
                root=root,
                timeout=600,
                log=log,
                datasets=STATS_DATASETS,
                heartbeat=heartbeat,
            )
        ]
        results.append(
            _run_player_stats_split(
                season,
                root=root,
                timeout=300,
                log=log,
                optional=True,
                heartbeat=heartbeat,
            )
        )
        return results

    if segment == "models":
        return _run_low_memory_phase(
            "refresh",
            season,
            root=root,
            only=MODEL_STEPS,
            timeout=600,
            log=log,
            heartbeat=heartbeat,
        )

    if segment == "content":
        return _run_low_memory_phase(
            "refresh",
            season,
            root=root,
            only=CONTENT_STEPS,
            timeout=600,
            log=log,
            heartbeat=heartbeat,
        )

    if segment == "news":
        return [_run_news_shard(season, timeout=600, log=log)]

    raise ValueError(f"unknown refresh segment: {segment}")


def _run_segment(segment: str, season: int, *, root: Path, instance: Path) -> dict:
    started = datetime.now(timezone.utc)
    lock = instance / "scheduled_refresh.lock"
    if not _acquire_lock(lock, started, 1):
        return {
            "status": "skipped",
            "reason": "refresh_already_running",
            "profile": segment,
            "season": season,
        }

    logs = instance / "refresh_logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"refresh-{started.strftime('%Y%m%dT%H%M%SZ')}.log"
    progress = {
        "season": season,
        "profile": segment,
        "started_at": started.isoformat(),
        "completed": False,
        "steps": {},
    }
    _write_progress(instance, progress)

    try:
        with log_path.open("w", encoding="utf-8") as log:
            print(
                f"segmented refresh: profile={segment} season={season} "
                f"parent_rss_mb={_rss_mb()}",
                file=log,
                flush=True,
            )
            results = _segment_results(
                segment,
                season,
                root=root,
                log=log,
                heartbeat=lambda: _touch_lock(lock),
            )
            for result in results:
                progress["steps"][str(result.get("step"))] = {
                    "status": str(result.get("status")),
                    "at": datetime.now(timezone.utc).isoformat(),
                    "message": str(result.get("message") or "")[:180],
                }
                _write_progress(instance, progress)

        _refresh_statistics(instance)
        finished = datetime.now(timezone.utc)
        required = [
            row
            for row in results
            if row.get("status") not in {"success", "skipped"}
            and not row.get("optional", False)
        ]
        degraded = [
            row
            for row in results
            if row.get("status") not in {"success", "skipped"}
            and row.get("optional", False)
        ]
        status = "failed" if required else "degraded" if degraded else "success"
        report = {
            "status": status,
            "profile": segment,
            "season": season,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "seconds": round((finished - started).total_seconds(), 1),
            "exit_code": 1 if required else 0,
            "log": str(log_path),
            "step_count": len(results),
            "degraded_steps": [
                {
                    "step": str(row.get("step")),
                    "status": str(row.get("status")),
                    "message": str(row.get("message") or "")[:240],
                }
                for row in degraded
            ],
            "degraded_count": len(degraded),
            "required_failure_count": len(required),
            "parent_peak_rss_mb": _rss_mb(),
            "child_peak_rss_mb": _children_rss_mb(),
            "resumed_steps": [],
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
    parser = argparse.ArgumentParser(
        description="Run a bounded scheduled refresh with low production overhead"
    )
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument(
        "--profile",
        choices=tuple(sorted(REFRESH_PROFILES)),
        default="light",
    )
    args = parser.parse_args(argv)

    database = Path(
        (os.getenv("CFB_DATABASE_PATH") or "").strip()
        or root / "instance" / "cfb.sqlite3"
    )
    if not database.is_absolute():
        database = root / database
    instance = database.parent

    if args.profile in {"scores", "results"}:
        report = run_scheduled_refresh(
            args.season,
            profile=args.profile,
            repo_root=root,
        )
    else:
        segment = "news" if args.profile == "news" else _segment_for_light()
        if args.profile == "heavy":
            segment = "core"
        report = _run_segment(segment, args.season, root=root, instance=instance)

    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") == "skipped" else int(report.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
