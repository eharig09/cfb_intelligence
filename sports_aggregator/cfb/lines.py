"""Betting lines as market opinion, stored per provider and never blended.

A line is the most liquid public forecast of a game, which makes it a useful
check on our own models -- and a genuinely different kind of information from a
report or a grade. It is stored the way it arrives: one row per provider, with
opening and current numbers kept apart so line movement stays visible.

Providers are not averaged into a "true" number. When DraftKings and Bovada
disagree, that disagreement is the information.

Nothing here is presented as a prediction by this application, and no betting
advice is derived from it.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Iterable

from sports_aggregator.cfb.repository import CFBRepository, _numeric


LINES_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_lines (
 game_id INTEGER NOT NULL, season INTEGER NOT NULL, provider TEXT NOT NULL,
 spread REAL, spread_open REAL, over_under REAL, over_under_open REAL,
 home_moneyline INTEGER, away_moneyline INTEGER,
 formatted_spread TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL,
 PRIMARY KEY(game_id,provider)
);
CREATE INDEX IF NOT EXISTS idx_game_lines_game ON game_lines(game_id);
"""


def initialize(repository: CFBRepository) -> None:
    repository.initialize()
    with closing(repository._connect()) as connection:
        connection.executescript(LINES_SCHEMA)


def store_lines(repository: CFBRepository, season: int, payload: Iterable[dict[str, Any]]) -> int:
    """Persist every provider quote attached to a game."""
    initialize(repository)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for game in payload:
        game_id = game.get("id")
        if game_id is None:
            continue
        for line in game.get("lines") or []:
            provider = str(line.get("provider") or "").strip()
            if not provider:
                continue
            rows.append((
                int(game_id), season, provider,
                _numeric(line.get("spread")), _numeric(line.get("spreadOpen")),
                _numeric(line.get("overUnder")), _numeric(line.get("overUnderOpen")),
                line.get("homeMoneyline"), line.get("awayMoneyline"),
                str(line.get("formattedSpread") or ""), now,
            ))
    with closing(repository._connect()) as connection:
        connection.executemany(
            """INSERT INTO game_lines VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id,provider) DO UPDATE SET
               spread=excluded.spread,spread_open=excluded.spread_open,
               over_under=excluded.over_under,over_under_open=excluded.over_under_open,
               home_moneyline=excluded.home_moneyline,away_moneyline=excluded.away_moneyline,
               formatted_spread=excluded.formatted_spread,fetched_at=excluded.fetched_at""",
            rows)
        connection.commit()
    return len(rows)


def game_lines(repository: CFBRepository, game_id: int) -> dict[str, Any]:
    """Every provider quote for one game, with movement and disagreement."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM game_lines WHERE game_id=? ORDER BY provider", (game_id,))]
    for row in rows:
        if row["spread"] is not None and row["spread_open"] is not None:
            row["spread_move"] = round(row["spread"] - row["spread_open"], 1)
        else:
            row["spread_move"] = None
        if row["over_under"] is not None and row["over_under_open"] is not None:
            row["total_move"] = round(row["over_under"] - row["over_under_open"], 1)
        else:
            row["total_move"] = None
    spreads = [row["spread"] for row in rows if row["spread"] is not None]
    totals = [row["over_under"] for row in rows if row["over_under"] is not None]
    return {
        "providers": rows,
        "count": len(rows),
        # Disagreement between books is information, so it is reported rather
        # than collapsed into a single consensus number.
        "spread_range": (round(max(spreads) - min(spreads), 1) if len(spreads) > 1 else 0.0),
        "total_range": (round(max(totals) - min(totals), 1) if len(totals) > 1 else 0.0),
        "consensus_spread": (round(sum(spreads) / len(spreads), 1) if spreads else None),
        "consensus_total": (round(sum(totals) / len(totals), 1) if totals else None),
    }


def lines_by_game(repository: CFBRepository, season: int) -> dict[int, dict[str, Any]]:
    """Consensus spread and total per game, for slate-level views."""
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            """SELECT game_id,AVG(spread) spread,AVG(over_under) total,COUNT(*) books
               FROM game_lines WHERE season=? GROUP BY game_id""", (season,)).fetchall()
    return {
        row["game_id"]: {
            "spread": round(row["spread"], 1) if row["spread"] is not None else None,
            "total": round(row["total"], 1) if row["total"] is not None else None,
            "books": row["books"],
        }
        for row in rows
    }
