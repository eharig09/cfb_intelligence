"""Populate sourced player injury annotations from ESPN athlete news.

Examples:
    python -m sports_aggregator.cfb.player_injury_cli --season 2026 --player-id 4427455
    python -m sports_aggregator.cfb.player_injury_cli --season 2026 --team-id 130
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.player_injuries import sync_player
from sports_aggregator.cfb.repository import CFBRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich CFB players with sourced ESPN injury history")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--player-id", default=None, help="One CFBD player ID")
    parser.add_argument("--team-id", type=int, default=None, help="All eligible players on one current roster")
    parser.add_argument("--database", default=None, help="Override CFB_DATABASE_PATH")
    return parser


def _players(repository: CFBRepository, season: int, player_id: str | None, team_id: int | None) -> list[dict]:
    if player_id:
        player = repository.get_player(str(player_id), int(season))
        return [player] if player else []
    if team_id is not None:
        team = repository.get_team(int(team_id))
        if not team:
            return []
        roster = repository.team_roster(team["school"], int(season))
        players = []
        for row in roster:
            raw_id = row.get("player_id")
            if raw_id is None:
                continue
            player = repository.get_player(str(raw_id), int(season))
            if player:
                players.append(player)
        return players
    raise SystemExit("Provide --player-id or --team-id")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    database = args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    reports = []
    for player in _players(repository, args.season, args.player_id, args.team_id):
        report = sync_player(repository, player, args.season)
        reports.append(report)
        print(
            f"{player.get('name')}: stored={report.get('stored', 0)} "
            f"espn={report.get('espn_athlete_id') or '—'} "
            f"status={report.get('skipped') or 'ok'}"
        )
    print(json.dumps({
        "season": args.season,
        "players": len(reports),
        "stored": sum(int(item.get("stored") or 0) for item in reports),
        "skipped": sum(1 for item in reports if item.get("skipped")),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
