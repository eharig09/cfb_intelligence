"""One entry point for building and refreshing the whole data store.

Three commands, distinguished by how often the underlying data actually changes:

* ``initial`` -- everything, including the static and historical datasets that
  only need fetching once (venues, prior seasons, promoted-team history).
* ``refresh`` -- only what moves: the current season, betting lines, weather,
  source ingestion and scoring. Safe to schedule.
* ``status`` -- row counts, freshness and failures across every source.

Every step is isolated. A step that fails records the failure and the run
continues, because one unavailable secondary source must not stop the rest of
the store from updating. The exit code reflects whether anything failed, so a
scheduler can still alert.
"""
"""One-command bootstrap, refresh, PFF import, and status utilities."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from typing import Any, Callable

from dotenv import load_dotenv


@dataclass
class Step:
    """One ingestion step, and when it is worth running."""

    name: str
    description: str
    command: list[str]
    phases: tuple[str, ...]
    optional: bool = False
    requires_env: tuple[str, ...] = field(default_factory=tuple)


def steps(season: int) -> list[Step]:
    """The full ingestion plan, in dependency order."""
    year = str(season)
    return [
        # --- canonical structure: everything else keys to these ---
        Step("cfbd-sync", "Teams, games, records, rankings, stats",
             ["sports_aggregator.cfb.cli", "sync", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-venues", "Stadium coordinates, elevation and domes",
             ["sports_aggregator.cfb.cli", "sync-venues", "--year", year],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-roster-context", "Prior roster, portal, draft, returning production",
             ["sports_aggregator.cfb.cli", "sync-roster-context", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-player-stats", "Current player statistics",
             ["sports_aggregator.cfb.cli", "sync-player-stats", "--year", str(season - 1)],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-backfill", "Prior seasons, for full career lines",
             ["sports_aggregator.cfb.cli", "backfill", "--year", year,
              "--from-year", str(season - 7), "--to-year", str(season - 1)],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-promoted", "Prior-classification history for promoted teams",
             ["sports_aggregator.cfb.cli", "sync-promoted", "--year", year],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-recruits", "Signing class ratings",
             ["sports_aggregator.cfb.cli", "sync-recruits", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-lines", "Betting lines from CFBD",
             ["sports_aggregator.cfb.cli", "sync-lines", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("transfer-grades", "Confirm PFF identities using portal evidence",
             ["sports_aggregator.cfb.cli", "link-transfer-grades", "--year", year],
             ("initial", "refresh")),

        # --- secondary structured sources ---
        Step("fpi", "ESPN FPI projections via SportsDataverse",
             ["sports_aggregator.cfb.external_cli", "fpi", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("weather", "Kickoff weather forecasts from Open-Meteo",
             ["sports_aggregator.cfb.external_cli", "weather", "--season", year],
             ("refresh",), optional=True),

        # --- reporting and discovery ---
        Step("bluesky", "Curated Bluesky author feeds",
             ["sports_aggregator.social.content_cli", "ingest", "--season", year],
             ("refresh",), optional=True),
        Step("reddit", "Curated subreddit submissions",
             ["sports_aggregator.social.content_cli", "ingest-reddit", "--season", year],
             ("refresh",), optional=True,
             requires_env=("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")),
        Step("youtube", "Verified channel uploads",
             ["sports_aggregator.social.content_cli", "ingest-youtube", "--season", year],
             ("refresh",), optional=True, requires_env=("YOUTUBE_API_KEY", "YOUTUBE_API")),
        Step("podcasts", "Verified podcast feeds",
             ["sports_aggregator.social.content_cli", "ingest-podcasts", "--season", year],
             ("refresh",), optional=True),
        Step("articles", "RSS reporting",
             ["sports_aggregator.social.content_cli", "ingest-reporting", "--season", year],
             ("refresh",), optional=True),

        # --- derivation: cheap, and must run after ingestion ---
        Step("cluster", "Cross-source story clustering",
             ["sports_aggregator.social.content_cli", "cluster"], ("refresh",)),
        Step("score", "Relevance scoring",
             ["sports_aggregator.social.content_cli", "score"], ("refresh",)),
    ]


def _env_satisfied(step: Step) -> bool:
    """Whether at least one of a step's accepted variables is configured."""
    if not step.requires_env:
        return True
    return any((os.getenv(name) or "").strip() for name in step.requires_env)


def run_step(step: Step, *, timeout: int = 1800) -> dict[str, Any]:
    """Run one step in its own process so a crash cannot take the run down."""
    started = datetime.now(timezone.utc)
    if not _env_satisfied(step):
        return {"step": step.name, "status": "skipped",
                "message": f"needs one of {', '.join(step.requires_env)}",
                "seconds": 0.0}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", *step.command],
            capture_output=True, text=True, timeout=timeout)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        tail = (completed.stdout or completed.stderr or "").strip().splitlines()
        return {
            "step": step.name,
            "status": "success" if completed.returncode == 0 else "failed",
            "message": tail[-1][:200] if tail else "",
            "seconds": round(elapsed, 1),
        }
    except subprocess.TimeoutExpired:
        return {"step": step.name, "status": "timeout",
                "message": f"exceeded {timeout}s", "seconds": float(timeout)}
    except Exception as exc:
        return {"step": step.name, "status": "failed",
                "message": str(exc)[:200], "seconds": 0.0}


def run_phase(phase: str, season: int, *, only: list[str] | None = None,
              timeout: int = 1800) -> list[dict[str, Any]]:
    """Run every step belonging to a phase, isolating each one."""
    plan = [step for step in steps(season) if phase in step.phases]
    if only:
        plan = [step for step in plan if step.name in set(only)]
    results = []
    for step in plan:
        print(f"[ ] {step.name}: {step.description}")
        result = run_step(step, timeout=timeout)
        marker = {"success": "[ok]", "skipped": "[--]"}.get(result["status"], "[!!]")
        print(f"{marker} {result['status']} ({result['seconds']}s) {result['message']}")
        results.append(result)
    return results


def status_report(season: int) -> dict[str, Any]:
    """Row counts and freshness across canonical and secondary sources."""
    from sports_aggregator.cfb.external import import_status
    from sports_aggregator.cfb.repository import CFBRepository
    from sports_aggregator.social.content import ContentRepository

    database = os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    report: dict[str, Any] = {"season": season, "database": database}
    for name, produce in (
        ("cfbd", lambda: repository.status(season)),
        ("stat_coverage", lambda: {"gaps": repository.stat_coverage()["gap_count"]}),
        ("secondary", lambda: import_status(repository, limit=12)),
        ("content", lambda: ContentRepository(database).summary()),
    ):
        # One unreadable section must not blank the whole report.
        try:
            report[name] = produce()
        except Exception as exc:
            report[name] = {"error": str(exc)[:200]}
    return report


def main(argv=None) -> int:
    load_dotenv()
    # A scheduled run should never die on an un-encodable character.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Build and refresh the data store")
    parser.add_argument("command", choices=("initial", "refresh", "status", "plan"))
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--only", nargs="*", default=None,
                        help="Run only these named steps")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status_report(args.season), indent=2, default=str))
        return 0

    if args.command == "plan":
        for step in steps(args.season):
            phases = ",".join(step.phases)
            flag = " (optional)" if step.optional else ""
            print(f"  {step.name:22s} [{phases:15s}] {step.description}{flag}")
        return 0

    results = run_phase(args.command, args.season, only=args.only, timeout=args.timeout)
    failures = [row for row in results if row["status"] not in ("success", "skipped")]
    print(f"\n{args.command}: {len(results)} steps, {len(failures)} failed")
    for row in failures:
        print(f"   FAILED {row['step']}: {row['message']}")
    return 1 if failures else 0
import subprocess
import sys


def run(*args: str, allow_failure: bool = False) -> bool:
    """Run one project CLI command using the current Python interpreter."""
    command = [sys.executable, "-m", *args]
    print("\n" + "=" * 72)
    print("$", " ".join(command))
    print("=" * 72)
    result = subprocess.run(command)
    if result.returncode != 0:
        print(f"FAILED ({result.returncode}): {' '.join(args)}")
        if not allow_failure:
            raise SystemExit(result.returncode)
        return False
    return True


def initial() -> None:
    """First-time production database population."""
    run("sports_aggregator.cfb.cli", "sync", "--year", "2026")
    run("sports_aggregator.cfb.cli", "sync-player-stats", "--year", "2025")
    run("sports_aggregator.cfb.cli", "sync-roster-context", "--year", "2026")

    # PFF must follow roster/context sync so player matching has the 2026 roster.
    run(
        "sports_aggregator.cfb.pff_cli",
        "import",
        "--season", "2025",
        "--roster-season", "2026",
        "--directory", "PFF",
        allow_failure=True,
    )

    run("sports_aggregator.social.cli", "seed")
    run("sports_aggregator.social.cli", "resolve", allow_failure=True)
    run("sports_aggregator.social.cli", "prepare")
    run("sports_aggregator.social.cli", "validate-reddit", allow_failure=True)
    run("sports_aggregator.social.media_cli", "validate-all", allow_failure=True)

    run(
        "sports_aggregator.social.content_cli",
        "ingest", "--season", "2026", "--limit", "10",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reddit", "--season", "2026", "--limit", "25",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-youtube", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-podcasts", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reporting", "--season", "2026",
        allow_failure=True,
    )

    run(
        "sports_aggregator.cfb.prospects_cli",
        "2027_nfl_mock_draft_database_top_100.csv",
        "--draft-year", "2027",
        "--roster-season", "2026",
        "--source", "mock_draft_database_consensus",
        allow_failure=True,
    )

    run("sports_aggregator.social.content_cli", "retag", "--season", "2026")
    run("sports_aggregator.social.content_cli", "cluster")
    run("sports_aggregator.social.content_cli", "score")
    print("\nBootstrap complete.")


def refresh() -> None:
    """Routine current-data and content refresh."""
    run("sports_aggregator.cfb.cli", "sync", "--year", "2026", allow_failure=True)
    run(
        "sports_aggregator.social.content_cli",
        "ingest", "--season", "2026", "--limit", "10",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reddit", "--season", "2026", "--limit", "25",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-youtube", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-podcasts", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reporting", "--season", "2026",
        allow_failure=True,
    )
    run("sports_aggregator.social.content_cli", "retag", "--season", "2026")
    run("sports_aggregator.social.content_cli", "cluster")
    run("sports_aggregator.social.content_cli", "score")
    print("\nRefresh complete.")


def pff() -> None:
    """Re-import PFF data without re-running the rest of the bootstrap."""
    run(
        "sports_aggregator.cfb.pff_cli",
        "import",
        "--season", "2025",
        "--roster-season", "2026",
        "--directory", "PFF",
    )
    print("\nPFF import complete.")


def status() -> None:
    """Print a compact status view across the main data layers."""
    run("sports_aggregator.cfb.cli", "status", "--year", "2026", allow_failure=True)
    run("sports_aggregator.social.cli", "status", allow_failure=True)
    run("sports_aggregator.social.cli", "unified-status", allow_failure=True)
    run("sports_aggregator.social.media_cli", "status", allow_failure=True)
    run("sports_aggregator.social.content_cli", "status", "--limit", "50", allow_failure=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CFB Intelligence bootstrap utility")
    parser.add_argument("command", choices=("initial", "refresh", "pff", "status"))
    args = parser.parse_args(argv)

    if args.command == "initial":
        initial()
    elif args.command == "refresh":
        refresh()
    elif args.command == "pff":
        pff()
    else:
        status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
