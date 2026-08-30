"""Precomputed quarterback air-yard and passing-efficiency summaries.

The source descriptions commonly identify the passer before the word ``pass``.
We resolve that token conservatively against rostered quarterbacks for the same
team/season. Numeric air yards come only from play-detail-v3 catch-spot parsing;
we never infer a continuous throw depth from short/deep wording alone.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import re
from typing import Any

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.play_detail import PARSER_VERSION

METRIC_VERSION = "qb-air-yards-v1"
MODEL_VERSION = "ep-v1"

_PASSER = re.compile(r"(?:^|\s)(?:#\d+\s+)?([A-Za-z][A-Za-z.'’\-]*(?:\s+[A-Za-z][A-Za-z.'’\-]*)?)\s+pass(?:es|ed|ing)?\b", re.I)


def initialize(repository) -> None:
    from sports_aggregator.cfb.play_detail import initialize as initialize_detail
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_ep
    initialize_detail(repository)
    initialize_ep(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_qb_air_yards_game (
          game_id INTEGER NOT NULL,
          season INTEGER NOT NULL,
          team TEXT NOT NULL,
          opponent TEXT NOT NULL,
          player_id TEXT,
          player_name TEXT NOT NULL,
          parser_version TEXT NOT NULL,
          model_version TEXT NOT NULL,
          metric_version TEXT NOT NULL,
          attributed_pass_plays INTEGER NOT NULL,
          measured_completions INTEGER NOT NULL,
          measured_air_yards REAL,
          measured_adot REAL,
          yards_after_catch REAL,
          yac_per_completion REAL,
          pass_epa REAL,
          epa_per_attributed_pass REAL,
          behind_line_plays INTEGER NOT NULL DEFAULT 0,
          short_plays INTEGER NOT NULL DEFAULT 0,
          intermediate_plays INTEGER NOT NULL DEFAULT 0,
          deep_plays INTEGER NOT NULL DEFAULT 0,
          numeric_depth_coverage REAL NOT NULL,
          built_at TEXT NOT NULL,
          PRIMARY KEY(game_id,team,player_name,parser_version,model_version,metric_version),
          FOREIGN KEY(game_id) REFERENCES games(game_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_qb_air_yards_game_lookup
          ON cfb_qb_air_yards_game(game_id,team,metric_version);
        CREATE INDEX IF NOT EXISTS idx_cfb_qb_air_yards_player
          ON cfb_qb_air_yards_game(player_id,season,metric_version);
        """)
        connection.commit()


def _passer_token(text: Any) -> str | None:
    match = _PASSER.search(str(text or ""))
    return match.group(1).strip() if match else None


def _name_parts(value: str) -> list[str]:
    return normalize_alias(value).split()


def _resolve_qb(token: str | None, qbs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not token or not qbs:
        return None
    parts = _name_parts(token)
    if not parts:
        return None
    token_last = parts[-1]
    token_first = parts[0] if len(parts) > 1 else ""
    matches: list[dict[str, Any]] = []
    for qb in qbs:
        first = normalize_alias(str(qb.get("first_name") or ""))
        last = normalize_alias(str(qb.get("last_name") or ""))
        full = normalize_alias(f"{first} {last}")
        normalized_token = normalize_alias(token)
        exact = normalized_token == full
        last_match = token_last == last
        initial_match = bool(token_first and first and token_first[0] == first[0])
        first_match = bool(token_first and first and (token_first == first or initial_match))
        if exact or (last_match and (not token_first or first_match)):
            matches.append(qb)
    return matches[0] if len(matches) == 1 else None


def build(repository, *, from_season: int = 2025, to_season: int | None = None,
          parser_version: str = PARSER_VERSION, model_version: str = MODEL_VERSION,
          metric_version: str = METRIC_VERSION) -> dict[str, Any]:
    initialize(repository)
    to_season = int(to_season if to_season is not None else from_season)
    from_season = int(from_season)
    now = datetime.now(timezone.utc).isoformat()

    with closing(repository._connect()) as connection:
        roster_rows = [dict(r) for r in connection.execute("""
          SELECT player_id,season,team,first_name,last_name,position
          FROM players
          WHERE season BETWEEN ? AND ? AND UPPER(COALESCE(position,''))='QB'
        """, (from_season, to_season)).fetchall()]
    qbs_by_team: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in roster_rows:
        qbs_by_team[(int(row["season"]), str(row["team"]))].append(row)

    aggregates: dict[tuple[int, int, str, str, str, str], dict[str, Any]] = {}
    unmatched_pass_plays = 0
    attributed_pass_plays = 0

    with closing(repository._connect()) as connection:
        cursor = connection.execute("""
          SELECT p.game_id,p.season,p.offense AS team,p.defense AS opponent,p.play_text,
                 d.air_yards,d.yards_after_catch,d.pass_depth,e.epa
          FROM cfb_plays p
          JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
          JOIN cfb_play_detail d ON d.play_id=p.play_id AND d.parser_version=?
          JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=?
          WHERE p.season BETWEEN ? AND ? AND m.rush_pass='pass'
          ORDER BY p.season,p.game_id,p.drive_number,p.play_number
        """, (parser_version, model_version, from_season, to_season))
        for row in cursor:
            team = str(row["team"])
            qb = _resolve_qb(_passer_token(row["play_text"]), qbs_by_team.get((int(row["season"]), team), []))
            if qb is None:
                unmatched_pass_plays += 1
                continue
            attributed_pass_plays += 1
            name = f"{qb.get('first_name') or ''} {qb.get('last_name') or ''}".strip()
            key = (int(row["game_id"]), int(row["season"]), team, str(row["opponent"]), str(qb.get("player_id") or ""), name)
            item = aggregates.setdefault(key, {
                "attributed": 0, "measured": 0, "air": 0.0, "yac": 0.0, "epa": 0.0,
                "behind_line": 0, "short": 0, "intermediate": 0, "deep": 0,
            })
            item["attributed"] += 1
            if row["epa"] is not None:
                item["epa"] += float(row["epa"])
            if row["air_yards"] is not None:
                item["measured"] += 1
                item["air"] += float(row["air_yards"])
                if row["yards_after_catch"] is not None:
                    item["yac"] += float(row["yards_after_catch"])
            depth = str(row["pass_depth"] or "")
            if depth in {"behind_line", "short", "intermediate", "deep"}:
                item[depth] += 1

    rows: list[tuple[Any, ...]] = []
    for (game_id, season, team, opponent, player_id, player_name), item in aggregates.items():
        attributed = int(item["attributed"])
        measured = int(item["measured"])
        rows.append((
            game_id, season, team, opponent, player_id or None, player_name,
            parser_version, model_version, metric_version,
            attributed, measured,
            item["air"] if measured else None,
            item["air"] / measured if measured else None,
            item["yac"] if measured else None,
            item["yac"] / measured if measured else None,
            item["epa"] if attributed else None,
            item["epa"] / attributed if attributed else None,
            int(item["behind_line"]), int(item["short"]), int(item["intermediate"]), int(item["deep"]),
            measured / attributed if attributed else 0.0,
            now,
        ))

    with closing(repository._connect()) as connection:
        connection.execute("""DELETE FROM cfb_qb_air_yards_game
          WHERE parser_version=? AND model_version=? AND metric_version=?
            AND season BETWEEN ? AND ?""",
          (parser_version, model_version, metric_version, from_season, to_season))
        connection.executemany("""INSERT INTO cfb_qb_air_yards_game(
          game_id,season,team,opponent,player_id,player_name,parser_version,model_version,
          metric_version,attributed_pass_plays,measured_completions,measured_air_yards,
          measured_adot,yards_after_catch,yac_per_completion,pass_epa,epa_per_attributed_pass,
          behind_line_plays,short_plays,intermediate_plays,deep_plays,numeric_depth_coverage,built_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        connection.commit()

    return {
        "metric_version": metric_version,
        "parser_version": parser_version,
        "model_version": model_version,
        "from_season": from_season,
        "to_season": to_season,
        "quarterback_games": len(rows),
        "attributed_pass_plays": attributed_pass_plays,
        "unmatched_pass_plays": unmatched_pass_plays,
        "passer_attribution_rate": round(attributed_pass_plays / max(1, attributed_pass_plays + unmatched_pass_plays), 4),
        "measured_completions": sum(int(r[10]) for r in rows),
    }


def game_summary(repository, game_id: int, *, parser_version: str = PARSER_VERSION,
                 model_version: str = MODEL_VERSION,
                 metric_version: str = METRIC_VERSION) -> list[dict[str, Any]]:
    initialize(repository)
    with closing(repository._connect()) as connection:
        return [dict(r) for r in connection.execute("""
          SELECT * FROM cfb_qb_air_yards_game
          WHERE game_id=? AND parser_version=? AND model_version=? AND metric_version=?
          ORDER BY team,attributed_pass_plays DESC,player_name
        """, (int(game_id), parser_version, model_version, metric_version)).fetchall()]
