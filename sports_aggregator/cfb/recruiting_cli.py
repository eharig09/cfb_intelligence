"""Backfill CFBD recruiting classes used by player career pages.

Example:
    python -m sports_aggregator.cfb.recruiting_cli --from-year 2019 --to-year 2026
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.repository import CFBRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill CFBD high-school recruiting classes into SQLite"
    )
    parser.add_argument("--from-year", type=int, default=datetime.now().year - 7)
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    parser.add_argument("--database", default=None, help="Override CFB_DATABASE_PATH")
    parser.add_argument("--force", action="store_true", help="Bypass CFBD raw-response cache")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.from_year > args.to_year:
        raise SystemExit("--from-year must not be after --to-year")

    database = args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    client = CFBDClient(
        raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw")
    )
    if not client.configured:
        raise CFBDConfigurationError("CFBD_API_KEY is required for recruiting backfill")

    failures: list[int] = []
    total = 0
    for year in range(args.from_year, args.to_year + 1):
        try:
            rows = client.recruits(year, args.force)
            count = repository.replace_recruits(year, rows)
            total += count
            print(f"{year}: recruits={count}")
        except Exception as exc:
            failures.append(year)
            print(f"{year}: failed ({exc})")

    print(
        f"recruiting backfill {args.from_year}-{args.to_year} complete; "
        f"stored={total} failed_years={len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
