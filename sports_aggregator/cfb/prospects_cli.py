"""Import an external consensus draft board with provenance."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.prospects import import_board
from sports_aggregator.cfb.repository import CFBRepository


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="CSV with Rank, Player, School, Position columns")
    parser.add_argument("--draft-year", type=int, required=True)
    parser.add_argument("--roster-season", type=int, required=True)
    parser.add_argument("--source", default="consensus",
                        help="Identifier for the board, retained with every row")
    args = parser.parse_args(argv)
    repository = CFBRepository(os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    counts = import_board(repository, args.path, draft_year=args.draft_year,
                          source=args.source, roster_season=args.roster_season)
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
