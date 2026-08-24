"""Lock-safe entry point for unattended CFB refreshes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from dotenv import load_dotenv


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


def _degraded_steps(log_path: Path) -> list[dict[str, str]]:
    """Return bootstrap steps that failed even though the phase stayed non-fatal.

    Bootstrap prints a ``[ ] step-name: description`` line before each command and
    a ``[!!] failed`` or ``[!!] timeout`` result line afterward. A zero process
    exit code with one of those result markers means an optional step failed. That
    should not abort the data refresh, but it must not be reported as fully healthy.
    """
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    current_step = "unknown"
    degraded: list[dict[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith("[ ] "):
            heading = line[4:]
            current_step = heading.split(":", 1)[0].strip() or "unknown"
            continue
        if not line.startswith("[!!]"):
            continue
        remainder = line[4:].strip()
        status = remainder.split(" ", 1)[0].strip().casefold()
        if status not in {"failed", "timeout"}:
            continue
        degraded.append({
            "step": current_step,
            "status": status,
            "message": remainder[:240],
        })
    return degraded


def run_scheduled_refresh(
    season: int,
    *,
    repo_root: str | Path | None = None,
    stale_lock_hours: float = 6,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
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
    command = [sys.executable, "-m", "sports_aggregator.bootstrap",
               "refresh", "--season", str(season)]
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = runner(command, cwd=str(root), stdout=log,
                               stderr=subprocess.STDOUT, text=True)
        finished = datetime.now(timezone.utc)
        degraded_steps = _degraded_steps(log_path) if completed.returncode == 0 else []
        if completed.returncode != 0:
            status = "failed"
        elif degraded_steps:
            status = "degraded"
        else:
            status = "success"
        report = {
            "status": status,
            "season": season, "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "seconds": round((finished - started).total_seconds(), 1),
            "exit_code": completed.returncode,
            "log": str(log_path.relative_to(root)),
            "degraded_steps": degraded_steps,
            "degraded_count": len(degraded_steps),
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
    parser.add_argument("--stale-lock-hours", type=float, default=6)
    args = parser.parse_args(argv)
    report = run_scheduled_refresh(args.season, repo_root=root,
                                   stale_lock_hours=args.stale_lock_hours)
    print(json.dumps(report, sort_keys=True))
    if report["status"] == "skipped":
        return 0
    return int(report.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
