"""Timeout-resistant historical roster/player-stat refresh.

A season is split into one roster request plus one subprocess per conference.
That keeps a single slow CFBD conference response from wedging an entire season
or the main bootstrap workflow. Successful conferences remain stored, so reruns
are naturally resumable.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import subprocess
import sys

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.models import Player
from sports_aggregator.cfb.repository import CFBRepository


def _tail(stdout: str | None, stderr: str | None) -> str:
    lines = (stdout or stderr or "").strip().splitlines()
    return lines[-1][:220] if lines else ""


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Refresh one historical CFB season without letting one conference hang the run"
    )
    parser.add_argument("--year", type=int, default=datetime.now().year - 1)
    parser.add_argument("--force", action="store_true", help="Bypass CFBD raw-response cache")
    parser.add_argument("--database", default=None, help="Override CFB_DATABASE_PATH")
    parser.add_argument(
        "--conference-timeout", type=int, default=120,
        help="Maximum seconds allowed for each conference player-stat subprocess",
    )
    args = parser.parse_args(argv)

    database = args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    repository = CFBRepository(database)
    repository.initialize()
    client = CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
    if not client.configured:
        raise CFBDConfigurationError("CFBD_API_KEY is required for historical sync")

    failures: list[str] = []

    # Historical rosters are important for career/identity views, but this is a
    # single bounded HTTP request rather than the old all-conference loop.
    try:
        count = repository.replace_players(
            args.year,
            (Player.from_cfbd(item, args.year) for item in client.roster(args.year, args.force)),
        )
        print(f"{args.year} roster: success ({count})")
    except Exception as exc:
        failures.append("roster")
        print(f"{args.year} roster: failed ({str(exc)[:180]})")

    conferences = [
        item["conference"] for item in repository.conferences() if item.get("conference")
    ]
    if not conferences:
        print("No synchronized FBS conferences are available; run the current-season CFBD sync first.")
        return 1

    succeeded = 0
    for conference in conferences:
        command = [
            sys.executable, "-m", "sports_aggregator.cfb.cli", "sync-player-stats",
            "--year", str(args.year), "--conference", conference,
            "--database", database,
        ]
        if args.force:
            command.append("--force")
        print(f"[ ] {args.year} {conference}")
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=args.conference_timeout
            )
            if completed.returncode == 0:
                succeeded += 1
                print(f"[ok] {_tail(completed.stdout, completed.stderr)}")
            else:
                failures.append(conference)
                print(f"[!!] failed: {_tail(completed.stdout, completed.stderr)}")
        except subprocess.TimeoutExpired:
            failures.append(conference)
            print(f"[!!] timeout after {args.conference_timeout}s; continuing")
        except Exception as exc:
            failures.append(conference)
            print(f"[!!] failed: {str(exc)[:180]}")

    print(
        f"historical {args.year}: {succeeded}/{len(conferences)} conferences succeeded; "
        f"{len(failures)} failures"
    )
    if failures:
        print("retry needed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
