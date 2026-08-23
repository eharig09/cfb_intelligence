"""Discover, verify and plan polling for team subreddits."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.reddit import RedditCommunityClient
from sports_aggregator.social.team_reddit import (
    ALWAYS_ON_TIER, mark_failed, mark_verified, poll_plan, register,
)


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("register", "verify", "plan"))
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--file", default=None,
                        help="JSON list of {team_id, subreddit, tier} to register")
    parser.add_argument("--games", type=int, default=12)
    args = parser.parse_args(argv)
    repository = CFBRepository(os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))

    if args.command == "register":
        if not args.file:
            parser.error("--file is required for register")
        with open(args.file, encoding="utf-8") as handle:
            entries = json.load(handle)
        print(f"registered={register(repository, entries)}")
        return 0

    if args.command == "verify":
        from contextlib import closing
        with closing(repository._connect()) as connection:
            pending = [dict(row) for row in connection.execute(
                """SELECT s.team_id,s.subreddit,t.school FROM team_subreddits s
                   JOIN teams t USING(team_id)
                   WHERE s.verification_status<>'verified' ORDER BY t.school""")]
        client = RedditCommunityClient()
        verified = failed = 0
        for entry in pending:
            resolution = client.resolve(entry["subreddit"])
            if resolution.status == "verified":
                mark_verified(repository, entry["team_id"],
                              platform_id=resolution.platform_id or "",
                              subscribers=int((resolution.activity_score or 1) * 0))
                verified += 1
                print(f"  {entry['school']}: verified r/{entry['subreddit']}")
            else:
                mark_failed(repository, entry["team_id"], resolution.description or resolution.status)
                failed += 1
                print(f"  {entry['school']}: {resolution.status} for r/{entry['subreddit']}")
        print(f"verified={verified} failed={failed}")
        return 1 if failed else 0

    plan = poll_plan(repository, args.season, games=args.games)
    print(json.dumps({k: v for k, v in plan.items() if k != "subreddits"}, indent=2))
    for row in plan["subreddits"][:20]:
        print(f"  {row['team']:22s} {row['subreddit']:26s} {row['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
