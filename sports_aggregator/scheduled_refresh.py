"""Lock-safe, low-memory entry point for unattended CFB refreshes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
from typing import Any, Callable

from dotenv import load_dotenv

from sports_aggregator.bootstrap import _env_satisfied, steps


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


def _rss_mb() -> float:
    """Return this process's maximum resident set size in MiB on Linux/macOS."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return round(raw / divisor, 1)


def _children_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return round(raw / divisor, 1)


def _run_low_memory_phase(
    phase: str,
    season: int,
    *,
    only: list[str] | None = None,
    timeout: int = 1800,
    log,
) -> list[dict[str, Any]]:
    """Run each bootstrap step in isolation while streaming output to disk.

    Unlike bootstrap.run_step(), this never uses capture_output=True, so verbose
    API/ingestion commands cannot accumulate their entire stdout/stderr in RAM.
    """
    plan = [step for step in steps(season) if phase in step.phases]
    if only:
        wanted = set(only)
        plan = [step for step in plan if step.name in wanted]

    results: list[dict[str, Any]] = []
    for step in plan:
        started = datetime.now(timezone.utc)
        before_rss = _rss_mb()
        print(
            f"[ ] {step.name}: {step.description} "
            f"parent_rss_mb={before_rss}",
            file=log,
            flush=True,
        )

        if not _env_satisfied(step):
            requirements = list(step.requires_all_env)
            if step.requires_env:
                requirements.append("one of " + ", ".join(step.requires_env))
            result = {
                "step": step.name,
                "status": "skipped",
                "message": f"needs {', '.join(requirements)}",
                "seconds": 0.0,
                "optional": step.optional,
                "parent_rss_mb": before_rss,
                "child_peak_rss_mb": _children_rss_mb(),
            }
        else:
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", *step.command],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                )
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                result = {
                    "step": step.name,
                    "status": "success" if completed.returncode == 0 else "failed",
                    "message": f"exit code {completed.returncode}",
                    "seconds": round(elapsed, 1),
                    "optional": step.optional,
                    "parent_rss_mb": _rss_mb(),
                    "child_peak_rss_mb": _children_rss_mb(),
                }
            except subprocess.TimeoutExpired:
                result = {
                    "step": step.name,
                    "status": "timeout",
                    "message": f"exceeded {timeout}s",
                    "seconds": float(timeout),
                    "optional": step.optional,
                    "parent_rss_mb": _rss_mb(),
                    "child_peak_rss_mb": _children_rss_mb(),
                }
            except Exception as exc:
                result = {
                    "step": step.name,
                    "status": "failed",
                    "message": str(exc)[:240],
                    "seconds": 0.0,
                    "optional": step.optional,
                    "parent_rss_mb": _rss_mb(),
                    "child_peak_rss_mb": _children_rss_mb(),
                }

        marker = {"success": "[ok]", "skipped": "[--]"}.get(result["status"], "[!!]")
        print(
            f"{marker} {result['status']} ({result['seconds']}s) "
            f"parent_rss_mb={result['parent_rss_mb']} "
            f"child_peak_rss_mb={result['child_peak_rss_mb']} "
            f"{result['message']}",
            file=log,
            flush=True,
        )
        results.append(result)
    return results


def run_scheduled_refresh(
    season: int,
    *,
    profile: str = "heavy",
    repo_root: str | Path | None = None,
    stale_lock_hours: float = 6,
    phase_runner: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run a scheduled refresh with minimal concurrent Python process overhead."""
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
            print(
                f"scheduled refresh: profile={normalized_profile} season={season} "
                f"pid={os.getpid()} parent_rss_mb={_rss_mb()}",
                file=log,
                flush=True,
            )
            if phase_runner is None:
                results = _run_low_memory_phase(
                    "refresh", season, only=only, log=log
                )
            else:
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
        status = "failed" if required_failures else "degraded" if degraded_steps else "success"

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
            "parent_peak_rss_mb": _rss_mb(),
            "child_peak_rss_mb": _children_rss_mb(),
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
