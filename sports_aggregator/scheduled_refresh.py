"""Lock-safe, low-memory entry point for unattended CFB refreshes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
from typing import Any, Callable

from dotenv import load_dotenv

from sports_aggregator.bootstrap import _env_satisfied, steps


LIGHT_REFRESH_STEPS = [
    "cfbd-sync",
    "articles",
    "weather",
    "bluesky",
    "reddit",
    "youtube",
    "podcasts",
    "local-articles",
]

CFBD_DATASET_STEPS = [
    "teams",
    "players",
    "games",
    "betting_lines",
    "media",
    "records",
    "coaches",
    "rankings",
    "team_stats",
    "advanced_stats",
    "core_ratings",
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
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return round(raw / divisor, 1)


def _children_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0
    return round(raw / divisor, 1)


def _database_path(root: Path) -> Path:
    configured = (os.getenv("CFB_DATABASE_PATH") or "").strip()
    database = Path(configured) if configured else root / "instance" / "cfb.sqlite3"
    return database if database.is_absolute() else root / database


def _sqlite_values(database: Path, sql: str) -> list[str]:
    if not database.exists():
        return []
    try:
        with sqlite3.connect(database) as connection:
            return [str(row[0]) for row in connection.execute(sql).fetchall() if row[0]]
    except sqlite3.Error:
        return []


def _fbs_teams(root: Path) -> list[str]:
    return _sqlite_values(
        _database_path(root),
        "SELECT school FROM teams WHERE lower(classification)='fbs' ORDER BY school",
    )


def _conferences(root: Path) -> list[str]:
    return _sqlite_values(
        _database_path(root),
        "SELECT DISTINCT conference FROM teams WHERE conference IS NOT NULL ORDER BY conference",
    )


def _run_command(command: list[str], *, timeout: int, log) -> tuple[str, str, float]:
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", *command],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return (
            "success" if completed.returncode == 0 else "failed",
            f"exit code {completed.returncode}",
            round(elapsed, 1),
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"exceeded {timeout}s", float(timeout)
    except Exception as exc:
        return "failed", str(exc)[:240], 0.0


def _run_scoped_commands(
    label: str,
    scopes: list[str],
    command_for: Callable[[str], list[str]],
    *,
    timeout: int,
    log,
) -> list[str]:
    failures: list[str] = []
    total = len(scopes)
    for index, scope in enumerate(scopes, start=1):
        print(
            f"        [{label}] {index}/{total} {scope} start "
            f"parent_rss_mb={_rss_mb()} child_peak_rss_mb={_children_rss_mb()}",
            file=log,
            flush=True,
        )
        status, message, seconds = _run_command(
            command_for(scope), timeout=timeout, log=log
        )
        print(
            f"        [{label}] {index}/{total} {scope} {status} ({seconds}s) "
            f"parent_rss_mb={_rss_mb()} child_peak_rss_mb={_children_rss_mb()} {message}",
            file=log,
            flush=True,
        )
        if status != "success":
            failures.append(scope)
    return failures


def _run_cfbd_split(season: int, *, root: Path, timeout: int, log) -> dict[str, Any]:
    """Run CFBD datasets independently, with the roster split one team at a time."""
    started = datetime.now(timezone.utc)
    failures: list[str] = []

    for dataset in CFBD_DATASET_STEPS:
        print(
            f"    [cfbd] {dataset} start parent_rss_mb={_rss_mb()} "
            f"child_peak_rss_mb={_children_rss_mb()}",
            file=log,
            flush=True,
        )

        if dataset == "players":
            teams = _fbs_teams(root)
            if not teams:
                status, message, seconds = _run_command(
                    ["sports_aggregator.cfb.dataset_cli", "players", "--year", str(season)],
                    timeout=timeout,
                    log=log,
                )
                if status != "success":
                    failures.append("players")
            else:
                scoped_failures = _run_scoped_commands(
                    "roster",
                    teams,
                    lambda team: [
                        "sports_aggregator.cfb.dataset_cli",
                        "players",
                        "--year",
                        str(season),
                        "--team",
                        team,
                    ],
                    timeout=timeout,
                    log=log,
                )
                status = "failed" if scoped_failures else "success"
                message = (
                    f"failed teams: {', '.join(scoped_failures[:8])}"
                    if scoped_failures
                    else f"{len(teams)} team rosters complete"
                )
                seconds = 0.0
                if scoped_failures:
                    failures.append("players")
        else:
            status, message, seconds = _run_command(
                ["sports_aggregator.cfb.dataset_cli", dataset, "--year", str(season)],
                timeout=timeout,
                log=log,
            )
            if status != "success":
                failures.append(dataset)

        print(
            f"    [cfbd] {dataset} {status} ({seconds}s) "
            f"parent_rss_mb={_rss_mb()} child_peak_rss_mb={_children_rss_mb()} {message}",
            file=log,
            flush=True,
        )

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "step": "cfbd-sync",
        "status": "failed" if failures else "success",
        "message": (
            f"failed datasets: {', '.join(failures)}" if failures
            else f"{len(CFBD_DATASET_STEPS)} datasets complete; roster team-scoped"
        ),
        "seconds": round(elapsed, 1),
        "optional": False,
        "parent_rss_mb": _rss_mb(),
        "child_peak_rss_mb": _children_rss_mb(),
    }


def _run_player_stats_split(
    season: int, *, root: Path, timeout: int, log, optional: bool
) -> dict[str, Any]:
    """Run current player production one conference per Python interpreter."""
    started = datetime.now(timezone.utc)
    conferences = _conferences(root)
    if not conferences:
        status, message, seconds = _run_command(
            ["sports_aggregator.cfb.cli", "sync-player-stats", "--year", str(season)],
            timeout=timeout,
            log=log,
        )
        failures = [] if status == "success" else ["all"]
    else:
        failures = _run_scoped_commands(
            "player-stats",
            conferences,
            lambda conference: [
                "sports_aggregator.cfb.cli",
                "sync-player-stats",
                "--year",
                str(season),
                "--conference",
                conference,
            ],
            timeout=timeout,
            log=log,
        )
        status = "failed" if failures else "success"
        message = (
            f"failed conferences: {', '.join(failures[:8])}"
            if failures
            else f"{len(conferences)} conferences complete"
        )
        seconds = round((datetime.now(timezone.utc) - started).total_seconds(), 1)

    return {
        "step": "cfbd-current-player-stats",
        "status": status,
        "message": message,
        "seconds": seconds,
        "optional": optional,
        "parent_rss_mb": _rss_mb(),
        "child_peak_rss_mb": _children_rss_mb(),
    }


def _run_low_memory_phase(
    phase: str,
    season: int,
    *,
    root: Path,
    only: list[str] | None = None,
    timeout: int = 1800,
    log,
) -> list[dict[str, Any]]:
    plan = [step for step in steps(season) if phase in step.phases]
    if only:
        wanted = set(only)
        plan = [step for step in plan if step.name in wanted]

    results: list[dict[str, Any]] = []
    for step in plan:
        before_rss = _rss_mb()
        print(
            f"[ ] {step.name}: {step.description} parent_rss_mb={before_rss}",
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
        elif step.name == "cfbd-sync":
            result = _run_cfbd_split(season, root=root, timeout=timeout, log=log)
        elif step.name == "cfbd-current-player-stats":
            result = _run_player_stats_split(
                season, root=root, timeout=timeout, log=log, optional=step.optional
            )
        else:
            status, message, seconds = _run_command(step.command, timeout=timeout, log=log)
            result = {
                "step": step.name,
                "status": status,
                "message": message,
                "seconds": seconds,
                "optional": step.optional,
                "parent_rss_mb": _rss_mb(),
                "child_peak_rss_mb": _children_rss_mb(),
            }

        marker = {"success": "[ok]", "skipped": "[--]"}.get(result["status"], "[!!]")
        print(
            f"{marker} {result['status']} ({result['seconds']}s) "
            f"parent_rss_mb={result['parent_rss_mb']} "
            f"child_peak_rss_mb={result['child_peak_rss_mb']} {result['message']}",
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
                    "refresh", season, root=root, only=only, log=log
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
            "log": str(log_path),
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
