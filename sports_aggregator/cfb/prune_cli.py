"""Report what a database is spending space on, and reclaim it in tiers.

Written against a measurement rather than an intuition. The obvious candidate --
old reporting and the links attached to it -- turns out to be a small share:
6,616 content items hold about 15 MB, against 2.97 million historical box-score
rows holding roughly 128 MB before indexes. Pruning every article ever ingested
would recover less than dropping one archived season.

So the tiers below are ordered by what they cost you, not by what they recover,
and each one says what it would free before touching anything:

* **raw payloads** -- `content_items.raw_json` is written on every ingest and
  read by nothing. `recent()` fetches it only to discard it. Blanking it for
  older rows loses no capability at all.
* **archived reporting** -- items past a retention window, with their topics,
  team and player links, relevance, roles and now-empty stories. Recoverable by
  re-ingesting, though the original URLs may have moved on.
* **archived seasons** -- per-player and per-team box scores and season stats
  for seasons before a cutoff. This is where the space actually is, and it is
  fully recoverable with `bootstrap history`, which is why it is last: it costs
  the most to rebuild.

Nothing here runs without being asked. The default is a dry run that prints the
report and exits.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from typing import Any

from sports_aggregator.cfb.repository import CFBRepository


#: Disk actually returned per historical row once the file is vacuumed,
#: measured rather than estimated: 959,662 rows freed 231 MB. Summing column
#: lengths gives roughly a fifth of this, because narrow rows are dominated by
#: index entries and page overhead.
BYTES_PER_HISTORICAL_ROW = 240

#: Tables holding per-season historical detail, and the column joining them to
#: a season. These are rebuilt by `bootstrap history`.
SEASON_TABLES = (
    ("game_player_box_stats", "game_id"),
    ("game_team_box_stats", "game_id"),
)

#: Content tables that do not cascade from `content_items` on their own.
STORY_TABLES = ("story_items", "story_teams", "story_players", "story_games")


def _scalar(connection, sql: str, params: tuple = ()) -> int:
    value = connection.execute(sql, params).fetchone()
    return int(value[0] or 0) if value else 0


def raw_payload_report(connection, older_than_days: int) -> dict[str, Any]:
    """Ingest payloads that nothing reads back."""
    rows = _scalar(connection, """SELECT COUNT(*) FROM content_items
                                  WHERE LENGTH(COALESCE(raw_json,'')) > 2
                                    AND published_at < date('now', ?)""",
                   (f"-{older_than_days} days",))
    megabytes = _scalar(connection, """SELECT COALESCE(SUM(LENGTH(raw_json)),0)
                                       FROM content_items
                                       WHERE LENGTH(COALESCE(raw_json,'')) > 2
                                         AND published_at < date('now', ?)""",
                        (f"-{older_than_days} days",)) / 1e6
    return {"tier": "raw payloads", "rows": rows, "megabytes": round(megabytes, 1),
            "loses": "nothing; the column is written and never read"}


def archived_reporting_report(connection, older_than_days: int) -> dict[str, Any]:
    items = _scalar(connection, """SELECT COUNT(*) FROM content_items
                                   WHERE published_at < date('now', ?)""",
                    (f"-{older_than_days} days",))
    megabytes = _scalar(connection, """SELECT COALESCE(SUM(
                                         LENGTH(COALESCE(title,''))
                                         + LENGTH(COALESCE(body_text,''))
                                         + LENGTH(COALESCE(summary,''))
                                         + LENGTH(COALESCE(raw_json,''))),0)
                                       FROM content_items
                                       WHERE published_at < date('now', ?)""",
                        (f"-{older_than_days} days",)) / 1e6
    return {"tier": "archived reporting", "rows": items,
            "megabytes": round(megabytes, 1),
            "loses": "articles past the window and their links; re-ingestable"}


def archived_seasons_report(connection, before_season: int) -> dict[str, Any]:
    total = 0
    for table, _ in SEASON_TABLES:
        total += _scalar(connection, f"""SELECT COUNT(*) FROM {table} b
                                         JOIN games g ON g.game_id=b.game_id
                                         WHERE g.season < ?""", (before_season,))
    total += _scalar(connection,
                     "SELECT COUNT(*) FROM player_season_stats WHERE season < ?",
                     (before_season,))
    # Calibrated against a real prune rather than guessed: removing 959,662
    # rows and vacuuming took an 888 MB database to 657 MB. A text-length
    # estimate said 41 MB, because rows this narrow are mostly index and page
    # overhead -- the part a byte count of the columns never sees.
    return {"tier": f"archived seasons (before {before_season})", "rows": total,
            "megabytes": round(total * BYTES_PER_HISTORICAL_ROW / 1e6, 1),
            "loses": "historical detail; rebuildable with `bootstrap history`"}


def report(repository: CFBRepository, *, raw_days: int, content_days: int,
           before_season: int) -> list[dict[str, Any]]:
    repository.initialize()
    with closing(repository._connect()) as connection:
        return [
            raw_payload_report(connection, raw_days),
            archived_reporting_report(connection, content_days),
            archived_seasons_report(connection, before_season),
        ]


def prune_raw_payloads(repository: CFBRepository, older_than_days: int) -> int:
    with repository.transaction() as connection:
        cursor = connection.execute(
            """UPDATE content_items SET raw_json='{}'
               WHERE LENGTH(COALESCE(raw_json,'')) > 2
                 AND published_at < date('now', ?)""",
            (f"-{older_than_days} days",))
        return cursor.rowcount


def _table_exists(connection, name: str) -> bool:
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone())


def prune_archived_reporting(repository: CFBRepository, older_than_days: int) -> int:
    """Delete old items, then the stories left with nothing behind them."""
    with repository.transaction() as connection:
        cursor = connection.execute(
            "DELETE FROM content_items WHERE published_at < date('now', ?)",
            (f"-{older_than_days} days",))
        removed = cursor.rowcount
        # Clustering is a separate step, so a database can hold content and no
        # stories at all. Pruning must work there too rather than assume the
        # whole pipeline has run.
        if not _table_exists(connection, "stories"):
            return removed
        # story_items cascades from content_items, so a story can be left with
        # no sources at all. Those describe nothing and are removed with it.
        connection.execute(
            """DELETE FROM stories WHERE story_id NOT IN
               (SELECT DISTINCT story_id FROM story_items)""")
        for table in STORY_TABLES:
            if _table_exists(connection, table):
                connection.execute(
                    f"""DELETE FROM {table} WHERE story_id NOT IN
                        (SELECT story_id FROM stories)""")
        return removed


def prune_archived_seasons(repository: CFBRepository, before_season: int) -> int:
    with repository.transaction() as connection:
        removed = 0
        for table, _ in SEASON_TABLES:
            cursor = connection.execute(
                f"""DELETE FROM {table} WHERE game_id IN
                    (SELECT game_id FROM games WHERE season < ?)""",
                (before_season,))
            removed += cursor.rowcount
        cursor = connection.execute(
            "DELETE FROM player_season_stats WHERE season < ?", (before_season,))
        removed += cursor.rowcount
        return removed


def vacuum(repository: CFBRepository) -> None:
    """Return freed pages to the filesystem.

    Deleting rows leaves free pages inside the file; without this the database
    does not shrink on disk, which is the number that matters against a quota.
    """
    with closing(repository._connect()) as connection:
        connection.isolation_level = None
        connection.execute("VACUUM")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report and reclaim database space, safest tier first.")
    parser.add_argument("--database",
                        default=os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    parser.add_argument("--raw-days", type=int, default=30,
                        help="blank unread ingest payloads older than this")
    parser.add_argument("--content-days", type=int, default=180,
                        help="delete reporting older than this")
    parser.add_argument("--before-season", type=int, default=2021,
                        help="drop per-game historical detail before this season")
    parser.add_argument("--apply", choices=("raw", "reporting", "seasons"),
                        action="append", default=[],
                        help="actually prune this tier; repeatable")
    parser.add_argument("--vacuum", action="store_true",
                        help="return freed pages to the filesystem afterwards")
    args = parser.parse_args(argv)

    repository = CFBRepository(args.database)
    size_before = os.path.getsize(args.database) / 1e6 if os.path.exists(args.database) else 0
    print(f"database: {args.database} ({size_before:.0f} MB)\n")

    for entry in report(repository, raw_days=args.raw_days,
                        content_days=args.content_days,
                        before_season=args.before_season):
        print(f"  {entry['tier']:38} {entry['rows']:>10,} rows  "
              f"~{entry['megabytes']:>7.1f} MB")
        print(f"    loses: {entry['loses']}")

    if not args.apply:
        print("\nDry run. Nothing was changed. Pass --apply raw / reporting / seasons.")
        return 0

    print()
    if "raw" in args.apply:
        print(f"  blanked payloads on {prune_raw_payloads(repository, args.raw_days):,} items")
    if "reporting" in args.apply:
        print(f"  removed {prune_archived_reporting(repository, args.content_days):,} archived items")
    if "seasons" in args.apply:
        print(f"  removed {prune_archived_seasons(repository, args.before_season):,} historical rows")
    if args.vacuum:
        print("  vacuuming (this rewrites the file and needs free disk equal to its size)")
        vacuum(repository)
    size_after = os.path.getsize(args.database) / 1e6
    print(f"\ndatabase: {size_after:.0f} MB "
          f"({size_before - size_after:+.0f} MB)"
          f"{'' if args.vacuum else '  -- pass --vacuum to release pages to disk'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
