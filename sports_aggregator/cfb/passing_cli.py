"""CLI for CFBD per-attempt passing detail: direction, depth, air yards, YAC."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.passing_plays import (
    coverage, sync_season, sync_week, team_season_splits,
)
from sports_aggregator.cfb.qb_air_yards import build_from_cfbd
from sports_aggregator.cfb.repository import CFBRepository


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CFBD passing detail (direction, depth, air yards)")
    p.add_argument("command", choices=("sync", "coverage", "splits", "build-qb"))
    p.add_argument("--year", type=int, default=datetime.now().year)
    p.add_argument("--week", type=int, default=None,
                   help="One week. Omitted, sync walks the whole season.")
    p.add_argument("--team", default=None, help="For `splits`.")
    p.add_argument("--force", action="store_true", help="Refetch instead of using the raw cache.")
    p.add_argument("--database", default=None)
    return p


def main(argv: list[str] | None = None, *, client=None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    repository = CFBRepository(
        args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))

    if args.command == "coverage":
        print(json.dumps(coverage(repository, args.year), indent=2))
        return 0

    if args.command == "build-qb":
        print(json.dumps(build_from_cfbd(
            repository, from_season=args.year, to_season=args.year), indent=2))
        return 0

    if args.command == "splits":
        if not args.team:
            raise SystemExit("--team is required for `splits`")
        print(json.dumps(team_season_splits(repository, args.team, args.year), indent=2))
        return 0

    try:
        client = client or CFBDClient(
            raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
    except CFBDConfigurationError as exc:
        raise SystemExit(str(exc)) from None

    if args.week is not None:
        report = sync_week(repository, client, season=args.year, week=args.week, force=args.force)
    else:
        report = sync_season(repository, client, season=args.year, force=args.force)
    print(json.dumps(report if args.week is not None
                     else {k: v for k, v in report.items() if k != "weeks"}, indent=2))
    return 1 if report.get("failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
