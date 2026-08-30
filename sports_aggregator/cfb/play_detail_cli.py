"""CLI for play-text coverage auditing, parsing and tendency aggregation."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.play_detail import PARSER_VERSION, build
from sports_aggregator.cfb.play_text_audit import audit
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.team_game_tendencies import build as build_tendencies


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CFB play text enrichment")
    p.add_argument("command", choices=("audit", "build", "build-tendencies"))
    p.add_argument("--year", type=int, default=datetime.now().year)
    p.add_argument("--from-year", type=int, default=None)
    p.add_argument("--to-year", type=int, default=None)
    p.add_argument("--parser-version", default=PARSER_VERSION)
    p.add_argument("--model-version", default="ep-v1")
    p.add_argument("--database", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    first = args.from_year or args.year
    last = args.to_year or args.year
    if first > last:
        raise SystemExit("--from-year must not be after --to-year")
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    if args.command == "audit":
        result = audit(repository, from_season=first, to_season=last)
    elif args.command == "build":
        result = build(repository, from_season=first, to_season=last, parser_version=args.parser_version)
    else:
        result = build_tendencies(
            repository,
            from_season=first,
            to_season=last,
            parser_version=args.parser_version,
            model_version=args.model_version,
        )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
