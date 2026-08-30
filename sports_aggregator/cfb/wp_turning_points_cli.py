"""Read-only diagnostics for postgame WP turning-point attribution."""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.wp_turning_points import scoring_event_diagnostics


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit scoring/turnover rows for a game")
    p.add_argument("--game-id", type=int, required=True)
    p.add_argument("--wp-model-version", default="wp-v2")
    p.add_argument("--ep-model-version", default="ep-v1")
    p.add_argument("--database", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    rows = scoring_event_diagnostics(
        repository,
        args.game_id,
        wp_model_version=args.wp_model_version,
        ep_model_version=args.ep_model_version,
    )
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
