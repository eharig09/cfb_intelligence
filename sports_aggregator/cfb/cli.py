"""Standalone CFBD sync/status command: python -m sports_aggregator.cfb.cli ..."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.models import Player
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.sync import CFBDataSync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize college-football data from CFBD")
    parser.add_argument("command", choices=("sync", "status", "sync-player-stats",
                                           "sync-roster-context", "backfill"))
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--force", action="store_true", help="Bypass raw-response cache")
    parser.add_argument("--basic", action="store_true", help="Skip advanced stats and CORE ratings")
    parser.add_argument("--conference", default=None,
                        help="Limit sync-player-stats to one conference display name")
    parser.add_argument("--database", default=None, help="Override CFB_DATABASE_PATH")
    parser.add_argument("--from-year", type=int, default=None,
                        help="First season for backfill (inclusive)")
    parser.add_argument("--to-year", type=int, default=None,
                        help="Last season for backfill (inclusive)")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    database = args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    if args.command == "status":
        print(json.dumps(repository.status(args.year), indent=2, default=str))
        return 0

    client = CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
    if not client.configured:
        raise CFBDConfigurationError("CFBD_API_KEY is required for sync")
    if args.command == "sync-roster-context":
        prior_year = args.year - 1
        datasets = (
            ("prior_roster", lambda: repository.replace_players(
                prior_year, (Player.from_cfbd(item, prior_year) for item in client.roster(prior_year, args.force))
            )),
            ("transfers", lambda: repository.replace_transfers(
                args.year, client.transfers(args.year, args.force)
            )),
            ("draft_picks", lambda: repository.replace_draft_picks(
                args.year, client.draft_picks(args.year, args.force)
            )),
            ("returning_production", lambda: repository.replace_returning_production(
                args.year, client.returning_production(args.year, args.force)
            )),
        )
        failures = []
        for name, operation in datasets:
            try:
                print(f"{name}: success ({operation()})")
            except Exception as exc:
                failures.append(name); print(f"{name}: failed ({exc})")
        return 1 if failures else 0
    if args.command == "backfill":
        # Careers span roughly five seasons, so a player who is a senior now was a
        # freshman well outside the current-season window. Backfilling rosters and
        # production together is what makes a full career visible on a player page.
        first = args.from_year or (args.year - 7)
        last = args.to_year or (args.year - 1)
        if first > last:
            parser.error("--from-year must not be after --to-year")
        catalog_year = last
        conferences = [item["conference"] for item in repository.conferences()]
        failures = []
        for year in range(first, last + 1):
            try:
                count = repository.replace_players(
                    year, (Player.from_cfbd(item, year)
                           for item in client.roster(year, args.force)))
                print(f"{year} roster: success ({count})")
            except Exception as exc:
                failures.append(f"{year} roster"); print(f"{year} roster: failed ({exc})")
            try:
                catalog = {
                    item["name"]: item.get("abbreviation") or item["name"]
                    for item in client.conferences(catalog_year, args.force)
                    if item.get("classification") == "fbs"
                }
            except Exception as exc:
                catalog = {}
                print(f"{year} conference catalog unavailable ({exc}); using display names")
            total = 0
            for conference in conferences:
                try:
                    rows = client.player_season_stats(
                        year, catalog.get(conference, conference), args.force)
                    total += repository.replace_player_stats(year, rows, conference)
                except Exception as exc:
                    failures.append(f"{year} {conference}")
                    print(f"{year} {conference}: failed ({exc})")
            print(f"{year} player stats: {total} rows")
        print(f"backfill {first}-{last} complete; {len(failures)} dataset failures")
        return 1 if failures else 0
    if args.command == "sync-player-stats":
        conferences=[item["conference"] for item in repository.conferences()]
        if args.conference:
            conferences = [
                name for name in conferences if name.casefold() == args.conference.casefold()
            ]
            if not conferences:
                parser.error(f"Unknown synchronized conference: {args.conference}")
        catalog = {
            item["name"]: item.get("abbreviation") or item["name"]
            for item in client.conferences(args.year, args.force)
            if item.get("classification") == "fbs"
        }
        total=0; failures=[]
        for conference in conferences:
            try:
                api_conference = catalog.get(conference, conference)
                rows=client.player_season_stats(args.year,api_conference,args.force)
                count=repository.replace_player_stats(args.year,rows,conference); total+=count
                print(f"{conference} [{api_conference}]: success ({count})")
            except Exception as exc:
                failures.append(conference); print(f"{conference}: failed ({exc})")
        print(f"player_stats: {total} rows across {len(conferences)-len(failures)} conferences")
        return 1 if failures else 0
    report = CFBDataSync(client, repository).sync(
        args.year, force=args.force, include_advanced=not args.basic
    )
    for dataset in report.datasets:
        suffix = f" — {dataset.message}" if dataset.message else ""
        print(f"{dataset.dataset}: {dataset.status} ({dataset.count}){suffix}")
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
