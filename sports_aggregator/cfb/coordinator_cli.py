"""Coordinator sync command.

Usage:
    python -m sports_aggregator.cfb.coordinator_cli --year 2026
    python -m sports_aggregator.cfb.coordinator_cli --from-year 2016 --to-year 2026
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.coordinators import sync_season
from sports_aggregator.cfb.repository import CFBRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize FBS offensive/defensive coordinators")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--from-year", type=int, default=None)
    parser.add_argument("--to-year", type=int, default=None)
    parser.add_argument("--database", default=None, help="Override CFB_DATABASE_PATH")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    first = args.from_year or args.year
    last = args.to_year or args.year
    if first > last:
        raise SystemExit("--from-year must not be after --to-year")

    database = args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    failures: list[str] = []
    reports = []
    for year in range(first, last + 1):
        try:
            report = sync_season(repository, year, timeout=args.timeout)
            reports.append(report)
            unresolved = report.get("unresolved") or []
            print(
                f"{year}: stored={report['stored']} "
                f"unresolved={len(unresolved)}"
            )
            if unresolved:
                print("  unresolved: " + ", ".join(unresolved))
        except Exception as exc:
            failures.append(str(year))
            print(f"{year}: failed ({exc})")

    print(json.dumps({
        "from_year": first,
        "to_year": last,
        "seasons": len(reports),
        "failures": failures,
        "stored": sum(int(report.get("stored") or 0) for report in reports),
    }, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
