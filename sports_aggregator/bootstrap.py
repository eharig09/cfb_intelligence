from __future__ import annotations
"""One entry point for building, refreshing, and repairing the CFB data store.

Phases:
* initial  -- complete normal app update: structured data, live ingestion and
              downstream processing; never historical player-stat backfills.
* refresh  -- current-season/live update; never prior-season history.
* history  -- historical games/team/coach context plus player-stat backfill,
              isolated one season per process.
* status   -- compact freshness/coverage report.
* plan     -- show the steps without running them.

Both initial and refresh are intentionally insulated from historical player-stat
work. A slow CFBD historical endpoint must never block betting lines, models,
current games, weather, reporting, or the rest of the normal update workflow.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from typing import Any

from dotenv import load_dotenv


@dataclass
class Step:
    name: str
    description: str
    command: list[str]
    phases: tuple[str, ...]
    optional: bool = False
    requires_env: tuple[str, ...] = field(default_factory=tuple)
    requires_all_env: tuple[str, ...] = field(default_factory=tuple)


def steps(season: int, *, history_from: int | None = None,
          history_to: int | None = None) -> list[Step]:
    year = str(season)
    plan: list[Step] = [
        Step("cfbd-sync", "Current teams, roster, games, lines, records, rankings and stats",
             ["sports_aggregator.cfb.cli", "sync", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-current-player-stats", "Current-season player production by conference",
             ["sports_aggregator.cfb.cli", "sync-player-stats", "--year", year],
             ("initial", "refresh"), optional=True, requires_env=("CFBD_API_KEY",)),
        Step("cfbd-models", "CFBD CORE ratings and ESPN FPI model data",
             ["sports_aggregator.cfb.models_cli", "sync", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-lines", "Fast market-only betting-line refresh",
             ["sports_aggregator.cfb.cli", "sync-lines", "--year", year, "--force"],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-venues", "Stadium coordinates, elevation and domes",
             ["sports_aggregator.cfb.cli", "sync-venues", "--year", year],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-roster-context", "Prior roster, portal, draft and returning production",
             ["sports_aggregator.cfb.cli", "sync-roster-context", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-prior-history", "Prior-season games, records, team stats and coaches",
             ["sports_aggregator.cfb.cli", "sync-history", "--year", str(season - 1)],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-prior-player-stats", "Prior-season roster and production baseline",
             ["sports_aggregator.cfb.history_cli", "--year", str(season - 1)],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-promoted", "Prior-classification history for promoted teams",
             ["sports_aggregator.cfb.cli", "sync-promoted", "--year", year],
             ("initial",), requires_env=("CFBD_API_KEY",)),
        Step("cfbd-recruits", "Signing class ratings",
             ["sports_aggregator.cfb.cli", "sync-recruits", "--year", year],
             ("initial", "refresh"), requires_env=("CFBD_API_KEY",)),
        Step("pff", "Import local PFF snapshot",
             ["sports_aggregator.cfb.pff_cli", "import", "--season", str(season - 1),
              "--roster-season", year, "--directory", "PFF"],
             ("initial",), optional=True),
        Step("transfer-grades", "Confirm PFF identities using portal evidence",
             ["sports_aggregator.cfb.cli", "link-transfer-grades", "--year", year],
             ("initial", "refresh")),
        Step("prospects", "Import consensus NFL draft board",
             ["sports_aggregator.cfb.prospects_cli", "2027_nfl_mock_draft_database_top_100.csv",
              "--draft-year", str(season + 1), "--roster-season", year,
              "--source", "mock_draft_database_consensus"],
             ("initial",), optional=True),
        Step("weather", "Kickoff weather forecasts from Open-Meteo",
             ["sports_aggregator.cfb.external_cli", "weather", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("social-seed", "Seed curated reporting/source registry",
             ["sports_aggregator.social.cli", "seed"], ("initial",), optional=True),
        Step("social-prepare", "Prepare curated social sources",
             ["sports_aggregator.social.cli", "prepare"], ("initial",), optional=True),
        Step("bluesky-resolve", "Resolve and verify curated Bluesky handles",
             ["sports_aggregator.social.cli", "resolve"],
             ("initial", "refresh"), optional=True),
        Step("reddit-validate", "Validate configured subreddit endpoints",
             ["sports_aggregator.social.cli", "validate-reddit"],
             ("initial", "refresh"), optional=True,
             requires_all_env=("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")),
        Step("media-seed", "Import versioned podcast and YouTube research catalog",
             ["sports_aggregator.social.media_cli", "seed"],
             ("initial", "refresh"), optional=True),
        Step("media-validate", "Validate/promote YouTube and podcast endpoints",
             ["sports_aggregator.social.media_cli", "validate-all"],
             ("initial", "refresh"), optional=True),
        Step("bluesky", "Curated Bluesky author feeds",
             ["sports_aggregator.social.content_cli", "ingest", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("reddit", "Curated subreddit submissions",
             ["sports_aggregator.social.content_cli", "ingest-reddit", "--season", year],
             ("initial", "refresh"), optional=True,
             requires_all_env=("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")),
        Step("youtube", "Verified channel uploads",
             ["sports_aggregator.social.content_cli", "ingest-youtube", "--season", year],
             ("initial", "refresh"), optional=True,
             requires_env=("YOUTUBE_API_KEY", "YOUTUBE_API")),
        Step("podcasts", "Verified podcast feeds",
             ["sports_aggregator.social.content_cli", "ingest-podcasts", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("articles", "RSS reporting",
             ["sports_aggregator.social.content_cli", "ingest-reporting", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("local-articles", "Verified team-scoped local reporting",
             ["sports_aggregator.social.content_cli", "ingest-local-reporting",
              "--season", year, "--limit", "15"],
             ("initial", "refresh"), optional=True),
        Step("retag", "Classify CFB eligibility, then re-resolve current entities",
             ["sports_aggregator.social.content_cli", "retag", "--season", year],
             ("initial", "refresh"), optional=True),
        Step("cluster", "Cross-source story clustering",
             ["sports_aggregator.social.content_cli", "cluster"],
             ("initial", "refresh"), optional=True),
        Step("score", "Relevance scoring",
             ["sports_aggregator.social.content_cli", "score"],
             ("initial", "refresh"), optional=True),
    ]

    first_history = history_from if history_from is not None else season - 7
    last_history = history_to if history_to is not None else season - 1
    for historical_year in range(first_history, last_history + 1):
        plan.append(Step(
            f"game-history-{historical_year}",
            f"Games, records, team stats and coaches for {historical_year}",
            ["sports_aggregator.cfb.cli", "sync-history", "--year", str(historical_year)],
            ("history",), requires_env=("CFBD_API_KEY",),
        ))
        plan.append(Step(
            f"history-{historical_year}",
            f"Player statistics and roster history for {historical_year}",
            ["sports_aggregator.cfb.history_cli", "--year", str(historical_year)],
            ("history",), requires_env=("CFBD_API_KEY",),
        ))
        plan.append(Step(
            f"box-history-{historical_year}",
            f"Cached team and player box scores for {historical_year}",
            ["sports_aggregator.cfb.cli", "sync-box-scores", "--year", str(historical_year)],
            ("history",), requires_env=("CFBD_API_KEY",),
        ))
    return plan


def _env_satisfied(step: Step) -> bool:
    alternatives_ok = (not step.requires_env or
                       any((os.getenv(name) or "").strip() for name in step.requires_env))
    required_ok = all((os.getenv(name) or "").strip() for name in step.requires_all_env)
    return alternatives_ok and required_ok


def run_step(step: Step, *, timeout: int = 1800) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    if not _env_satisfied(step):
        requirements = list(step.requires_all_env)
        if step.requires_env:
            requirements.append("one of " + ", ".join(step.requires_env))
        return {"step": step.name, "status": "skipped",
                "message": f"needs {', '.join(requirements)}", "seconds": 0.0}
    try:
        completed = subprocess.run([sys.executable, "-m", *step.command],
                                   capture_output=True, text=True, timeout=timeout)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        output = (completed.stdout or completed.stderr or "").strip().splitlines()
        return {"step": step.name,
                "status": "success" if completed.returncode == 0 else "failed",
                "message": output[-1][:240] if output else "", "seconds": round(elapsed, 1)}
    except subprocess.TimeoutExpired:
        return {"step": step.name, "status": "timeout",
                "message": f"exceeded {timeout}s", "seconds": float(timeout)}
    except Exception as exc:
        return {"step": step.name, "status": "failed",
                "message": str(exc)[:240], "seconds": 0.0}


def run_phase(phase: str, season: int, *, only: list[str] | None = None,
              timeout: int = 1800, history_from: int | None = None,
              history_to: int | None = None) -> list[dict[str, Any]]:
    plan = [step for step in steps(
        season, history_from=history_from, history_to=history_to) if phase in step.phases]
    if only:
        wanted = set(only)
        plan = [step for step in plan if step.name in wanted]
    results = []
    for step in plan:
        print(f"[ ] {step.name}: {step.description}")
        result = run_step(step, timeout=timeout)
        result["optional"] = step.optional
        marker = {"success": "[ok]", "skipped": "[--]"}.get(result["status"], "[!!]")
        print(f"{marker} {result['status']} ({result['seconds']}s) {result['message']}")
        results.append(result)
    return results


def status_report(season: int) -> dict[str, Any]:
    from sports_aggregator.cfb.external import import_status
    from sports_aggregator.cfb.repository import CFBRepository
    from sports_aggregator.social.content import ContentRepository

    database = os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    report: dict[str, Any] = {"season": season, "database": database}
    for name, produce in (
        ("cfbd", lambda: repository.status(season)),
        ("stat_coverage", lambda: repository.stat_coverage()),
        ("history_coverage", lambda: repository.history_coverage()),
        ("secondary", lambda: import_status(repository, limit=12)),
        ("content", lambda: ContentRepository(database).summary()),
    ):
        try:
            report[name] = produce()
        except Exception as exc:
            report[name] = {"error": str(exc)[:200]}
    return report


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Build and refresh the CFB Intelligence data store")
    parser.add_argument("command", choices=("initial", "refresh", "history", "status", "plan"))
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--only", nargs="*", default=None,
                        help="Run only named steps, e.g. history-2021 history-2022")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="Per-step timeout in seconds; history also enforces a shorter timeout per conference")
    parser.add_argument("--from-year", type=int, default=None,
                        help="First historical season to add (history/plan only)")
    parser.add_argument("--to-year", type=int, default=None,
                        help="Last historical season to add (history/plan only)")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status_report(args.season), indent=2, default=str))
        return 0
    if args.command == "plan":
        for step in steps(args.season, history_from=args.from_year, history_to=args.to_year):
            phases = ",".join(step.phases)
            flag = " (optional)" if step.optional else ""
            print(f"  {step.name:22s} [{phases:15s}] {step.description}{flag}")
        return 0

    if args.from_year is not None and args.to_year is not None and args.from_year > args.to_year:
        parser.error("--from-year must not be after --to-year")
    results = run_phase(args.command, args.season, only=args.only, timeout=args.timeout,
                        history_from=args.from_year, history_to=args.to_year)
    failures = [row for row in results
                if row["status"] not in ("success", "skipped") and not row["optional"]]
    print(f"\n{args.command}: {len(results)} steps, {len(failures)} failed")
    for row in failures:
        print(f"   FAILED {row['step']}: {row['message']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
