"""CLI for auditing stored CFB play-description coverage."""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.play_text_audit import audit
from sports_aggregator.cfb.repository import CFBRepository


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Audit stored CFBD play_text coverage and phrasing consistency")
    parser.add_argument("--from-year", type=int, default=None)
    parser.add_argument("--to-year", type=int, default=None)
    parser.add_argument("--patterns", type=int, default=20,
                        help="Number of common normalized text patterns to return")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    if args.from_year is not None and args.to_year is not None and args.from_year > args.to_year:
        parser.error("--from-year must not be after --to-year")
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    result = audit(
        repository,
        from_season=args.from_year,
        to_season=args.to_year,
        sample_patterns=args.patterns,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
