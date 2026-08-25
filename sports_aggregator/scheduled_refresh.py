"""Lock-safe entry point for unattended CFB refreshes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from sports_aggregator.bootstrap import run_phase


LIGHT_REFRESH_STEPS = [
    "cfbd-sync",
    "articles",
    "cfbd-lines",
    "weather",
    "bluesky",
    "reddit",
    "youtube",
    "podcasts",
    "local-articles",
]


def _acquire_lock(path: Path, started: datetime, stale_hours: float) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age < stale_hours * 3600:
            return False
        path.unlink(missing_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "started_at": started.isoformat()}, handle)
    return True


def run_scheduled_refresh(
    season: int,
    *,
    profile: str = "heavy",
    repo_root: str | Path | None = None,
    stale_lock_hours: float = 6,
    phase_runner: Callable[..., list[dict[str, Any]]] = run_phase,
) -> dict[str, Any]:
    """Run a scheduled refresh without spawning an intermediate Python process.

    ``light`` runs the latency-sensitive live-data jobs. ``heavy`` runs the full
    current-season refresh plan. Individual bootstrap steps still execute in
    isolated subprocesses so their memory is released between datasets.
    """
    normalized_profile = profile.strip().casefold()
    if normalized_profile not in {"light", "heavy"}:
        raise ValueError("profile must be 'light' or 'heavy'")

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    state_override = (os.getenv("CFB_REFRESH_STATE_PATH") or "").strip()
    database_override = (os.getenv("CFB_DATABASE_PATH") or "").strip()
    if state_override:
        instance = Path(state_override)
        if not instance.is_absolute():
            instance = root / instance
    elif database_override:
        database = Path(database_override)
        if not database.is_absolute():
            database = root / database
        instance = database.parent
    else:
        instance = root / "instance"

    logs = instance / "refresh_logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    lock = instance / "scheduled_refresh.lock"
    if not _acquire_lock(lock, started, stale_lock_hours):
        return {"status": "skipped", "reason": "refresh_already_running"}

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    log_path = logs / f"refresh-{stamp}.log"
    only = LIGHT_REFRESH_STEPS if normalized_profile == "light" else None

    try:
        with log_path.open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                print(
                    f"scheduled refresh: profile={normalized_profile} "
                    f"season={season} pid={os.getpid()}"
                )
                results = phase_runner("refresh", season, only=only)

        finished = datetime.now(timezone.utc)
        required_failures = [
            row for row in results
            if row.get("status") not in {"success", "skipped"}
            and not row.get("optional", False)
        ]
        degraded_steps = [
            {
                "step": str(row.get("step", "unknown")),
                "status": str(row.get("status", "failed")),
                "message": str(row.get("message", ""))[:240],
            }
            for row in results
            if row.get("status") not in {"success", "skipped"}
            and row.get("optional", False)
        ]
        exit_code = 1 if required_failures else 0
        if required_failures:
            status = "failed"
        elif degraded_steps:
            status = "degraded"
        else:
            status = "success"

        report = {
            "status": status,
            "profile": normalized_profile,
            "season": season,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "seconds": round((finished - started).total_seconds(), 1),
            "exit_code": exit_code,
            "log": str(log_path.relative_to(root)),
            "step_count": len(results),
            "degraded_steps": degraded_steps,
            "degraded_count": len(degraded_steps),
            "required_failure_count": len(required_failures),
        }
        history = instance / "scheduled_refresh_history.jsonl"
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, separators=(",", ":")) + "\n")
        return report
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description="Run one lock-safe scheduled refresh")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--profile", choices=("light", "heavy"), default="heavy")
    parser.add_argument("--stale-lock-hours", type=float, default=6)
    args = parser.parse_args(argv)
    report = run_scheduled_refresh(
        args.season,
        profile=args.profile,
        repo_root=root,
        stale_lock_hours=args.stale_lock_hours,
    )
    print(json.dumps(report, sort_keys=True))
    if report["status"] == "skipped":
        return 0
    return int(report.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
