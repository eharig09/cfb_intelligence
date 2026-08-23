"""One-command bootstrap, refresh, PFF import, and status utilities."""

from __future__ import annotations

import argparse
import subprocess
import sys


def run(*args: str, allow_failure: bool = False) -> bool:
    """Run one project CLI command using the current Python interpreter."""
    command = [sys.executable, "-m", *args]
    print("\n" + "=" * 72)
    print("$", " ".join(command))
    print("=" * 72)
    result = subprocess.run(command)
    if result.returncode != 0:
        print(f"FAILED ({result.returncode}): {' '.join(args)}")
        if not allow_failure:
            raise SystemExit(result.returncode)
        return False
    return True


def initial() -> None:
    """First-time production database population."""
    run("sports_aggregator.cfb.cli", "sync", "--year", "2026")
    run("sports_aggregator.cfb.cli", "sync-player-stats", "--year", "2025")
    run("sports_aggregator.cfb.cli", "sync-roster-context", "--year", "2026")

    # PFF must follow roster/context sync so player matching has the 2026 roster.
    run(
        "sports_aggregator.cfb.pff_cli",
        "import",
        "--season", "2025",
        "--roster-season", "2026",
        "--directory", "PFF",
        allow_failure=True,
    )

    run("sports_aggregator.social.cli", "seed")
    run("sports_aggregator.social.cli", "resolve", allow_failure=True)
    run("sports_aggregator.social.cli", "prepare")
    run("sports_aggregator.social.cli", "validate-reddit", allow_failure=True)
    run("sports_aggregator.social.media_cli", "validate-all", allow_failure=True)

    run(
        "sports_aggregator.social.content_cli",
        "ingest", "--season", "2026", "--limit", "10",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reddit", "--season", "2026", "--limit", "25",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-youtube", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-podcasts", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reporting", "--season", "2026",
        allow_failure=True,
    )

    run(
        "sports_aggregator.cfb.prospects_cli",
        "2027_nfl_mock_draft_database_top_100.csv",
        "--draft-year", "2027",
        "--roster-season", "2026",
        "--source", "mock_draft_database_consensus",
        allow_failure=True,
    )

    run("sports_aggregator.social.content_cli", "retag", "--season", "2026")
    run("sports_aggregator.social.content_cli", "cluster")
    run("sports_aggregator.social.content_cli", "score")
    print("\nBootstrap complete.")


def refresh() -> None:
    """Routine current-data and content refresh."""
    run("sports_aggregator.cfb.cli", "sync", "--year", "2026", allow_failure=True)
    run(
        "sports_aggregator.social.content_cli",
        "ingest", "--season", "2026", "--limit", "10",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reddit", "--season", "2026", "--limit", "25",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-youtube", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-podcasts", "--season", "2026", "--limit", "20",
        allow_failure=True,
    )
    run(
        "sports_aggregator.social.content_cli",
        "ingest-reporting", "--season", "2026",
        allow_failure=True,
    )
    run("sports_aggregator.social.content_cli", "retag", "--season", "2026")
    run("sports_aggregator.social.content_cli", "cluster")
    run("sports_aggregator.social.content_cli", "score")
    print("\nRefresh complete.")


def pff() -> None:
    """Re-import PFF data without re-running the rest of the bootstrap."""
    run(
        "sports_aggregator.cfb.pff_cli",
        "import",
        "--season", "2025",
        "--roster-season", "2026",
        "--directory", "PFF",
    )
    print("\nPFF import complete.")


def status() -> None:
    """Print a compact status view across the main data layers."""
    run("sports_aggregator.cfb.cli", "status", "--year", "2026", allow_failure=True)
    run("sports_aggregator.social.cli", "status", allow_failure=True)
    run("sports_aggregator.social.cli", "unified-status", allow_failure=True)
    run("sports_aggregator.social.media_cli", "status", allow_failure=True)
    run("sports_aggregator.social.content_cli", "status", "--limit", "50", allow_failure=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CFB Intelligence bootstrap utility")
    parser.add_argument("command", choices=("initial", "refresh", "pff", "status"))
    args = parser.parse_args(argv)

    if args.command == "initial":
        initial()
    elif args.command == "refresh":
        refresh()
    elif args.command == "pff":
        pff()
    else:
        status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
