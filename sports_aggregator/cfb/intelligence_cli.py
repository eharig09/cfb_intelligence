"""CLI for staged pregame snapshots and bounded play-by-play ingestion."""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient
from sports_aggregator.cfb.play_by_play import derive_week, game_advanced_summary, sync_recent_plays
from sports_aggregator.cfb.pregame_snapshots import capture_due, snapshots_for_game
from sports_aggregator.cfb.repository import CFBRepository


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Pregame snapshots and play-by-play intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshots = sub.add_parser("snapshot", help="Capture due immutable pregame stages")
    snapshots.add_argument("--season", type=int, required=True)

    plays = sub.add_parser("sync-plays", help="Sync and derive recent completed-week plays")
    plays.add_argument("--season", type=int, required=True)
    plays.add_argument("--recent-weeks", type=int, default=2)
    plays.add_argument("--force", action="store_true")

    derive = sub.add_parser("derive", help="Rebuild one week's derived metrics without refetching")
    derive.add_argument("--season", type=int, required=True)
    derive.add_argument("--week", type=int, required=True)

    game = sub.add_parser("game", help="Print one game's advanced play summary and snapshots")
    game.add_argument("--game-id", type=int, required=True)

    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))

    if args.command == "snapshot":
        result = capture_due(repository, season=args.season)
    elif args.command == "sync-plays":
        client = CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
        result = sync_recent_plays(
            repository, client, season=args.season,
            recent_weeks=max(1, args.recent_weeks), force=args.force,
        )
    elif args.command == "derive":
        result = derive_week(repository, season=args.season, week=args.week)
    else:
        result = {
            "advanced": game_advanced_summary(repository, args.game_id),
            "pregame_snapshots": snapshots_for_game(repository, args.game_id),
        }
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
