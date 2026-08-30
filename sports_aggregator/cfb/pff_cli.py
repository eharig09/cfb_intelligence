"""PFF snapshot import: python -m sports_aggregator.cfb.pff_cli ..."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.pff import pff_summary
from sports_aggregator.cfb.pff_flexible import (
    import_pff_directory_flexible,
    preflight_pff_directory,
)
from sports_aggregator.cfb.repository import CFBRepository


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Import historical PFF college-football CSVs")
    parser.add_argument("command", choices=("import", "preflight", "status"))
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--roster-season", type=int, default=2026)
    parser.add_argument("--directory", default="PFF")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)

    if args.command == "preflight":
        print(json.dumps(preflight_pff_directory(args.directory).as_dict(), indent=2, default=str))
        return 0

    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    if args.command == "status":
        print(json.dumps(pff_summary(repository, args.season), indent=2, default=str))
        return 0

    report = import_pff_directory_flexible(
        repository, args.directory, season=args.season, roster_season=args.roster_season
    )
    print(json.dumps(report.__dict__ if hasattr(report, "__dict__") else {
        field: getattr(report, field) for field in report.__dataclass_fields__
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
