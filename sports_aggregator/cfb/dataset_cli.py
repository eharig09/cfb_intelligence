"""Run one CFBD dataset sync per process to keep Render memory bounded."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.lines import store_lines
from sports_aggregator.cfb.models import Game, Player, PollRanking, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.sync import flatten_rankings


DATASETS = (
    "teams",
    "players",
    "games",
    "betting_lines",
    "media",
    "records",
    "coaches",
    "rankings",
    "team_stats",
    "advanced_stats",
    "core_ratings",
)


def sync_dataset(name: str, season: int, *, force: bool = False) -> int:
    database = os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    repository.initialize()
    client = CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
    if not client.configured:
        raise CFBDConfigurationError("CFBD_API_KEY is required for sync")

    if name == "teams":
        return repository.replace_teams(Team.from_cfbd(item) for item in client.teams(season, force))
    if name == "players":
        return repository.replace_players(
            season, (Player.from_cfbd(item, season) for item in client.roster(season, force))
        )
    if name == "games":
        return repository.replace_games(
            season, (Game.from_cfbd(item) for item in client.games(season, force))
        )
    if name == "betting_lines":
        return store_lines(repository, season, client.betting_lines(season, force))
    if name == "media":
        return repository.update_game_media(client.game_media(season, force))
    if name == "records":
        return repository.replace_records(
            season,
            (item for item in client.records(season, force)
             if item.get("classification") == "fbs"),
        )
    if name == "coaches":
        return repository.replace_coach_seasons(season, client.coaches(season, force))
    if name == "rankings":
        return repository.replace_rankings(season, flatten_rankings(client.rankings(season, force)))
    if name == "team_stats":
        return repository.replace_team_stats(season, client.team_stats(season, force))
    if name == "advanced_stats":
        return repository.replace_advanced_stats(season, client.advanced_team_stats(season, force))
    if name == "core_ratings":
        return repository.replace_core_ratings(season, client.core_ratings(season, force))
    raise ValueError(f"unknown dataset: {name}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Synchronize one CFBD dataset")
    parser.add_argument("dataset", choices=DATASETS)
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    count = sync_dataset(args.dataset, args.year, force=args.force)
    seconds = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"{args.dataset}: success ({count}) in {seconds:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
