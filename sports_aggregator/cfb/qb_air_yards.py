"""Precomputed quarterback air-yard and passing-efficiency summaries.

Passer attribution is deliberately conservative. Provider text commonly uses
compact names such as ``#12 M.Alejado pass ...``; jersey number, exact/initial
name matching, and explicit sack/spike grammar are used only when they resolve to
one rostered quarterback for that team and season. Numeric air yards come only
from play-detail-v3 catch-spot parsing.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import re
from typing import Any

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.play_detail import PARSER_VERSION
from sports_aggregator.cfb.repository import schema_once

METRIC_VERSION = "qb-air-yards-v2"
MODEL_VERSION = "ep-v2"

_NAME = r"[A-Za-z][A-Za-z.'’\-]*(?:\s+[A-Za-z][A-Za-z.'’\-]*)?"
_PASSER = re.compile(rf"(?:^|\s)(?:#(?P<jersey>\d+)\s+)?(?P<name>{_NAME})\s+pass(?:es|ed|ing)?\b", re.I)
_SACKED = re.compile(rf"(?:^|\s)(?:#(?P<jersey>\d+)\s+)?(?P<name>{_NAME})\s+(?:is\s+)?sacked\b", re.I)
_SPIKE = re.compile(rf"(?:^|\s)(?:#(?P<jersey>\d+)\s+)?(?P<name>{_NAME})\s+(?:spike|spikes|spiked)\b", re.I)


@schema_once("qb_air_yards")
def initialize(repository) -> None:
    from sports_aggregator.cfb.play_detail import initialize as initialize_detail
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_ep
    initialize_detail(repository); initialize_ep(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_qb_air_yards_game (
          game_id INTEGER NOT NULL, season INTEGER NOT NULL, team TEXT NOT NULL, opponent TEXT NOT NULL,
          player_id TEXT, player_name TEXT NOT NULL, parser_version TEXT NOT NULL, model_version TEXT NOT NULL,
          metric_version TEXT NOT NULL, attributed_pass_plays INTEGER NOT NULL, measured_completions INTEGER NOT NULL,
          measured_air_yards REAL, measured_adot REAL, yards_after_catch REAL, yac_per_completion REAL,
          pass_epa REAL, epa_per_attributed_pass REAL, behind_line_plays INTEGER NOT NULL DEFAULT 0,
          short_plays INTEGER NOT NULL DEFAULT 0, intermediate_plays INTEGER NOT NULL DEFAULT 0,
          deep_plays INTEGER NOT NULL DEFAULT 0, numeric_depth_coverage REAL NOT NULL, built_at TEXT NOT NULL,
          PRIMARY KEY(game_id,team,player_name,parser_version,model_version,metric_version),
          FOREIGN KEY(game_id) REFERENCES games(game_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_qb_air_yards_game_lookup ON cfb_qb_air_yards_game(game_id,team,metric_version);
        CREATE INDEX IF NOT EXISTS idx_cfb_qb_air_yards_player ON cfb_qb_air_yards_game(player_id,season,metric_version);
        """); connection.commit()


def _passer_identity(text: Any) -> tuple[str | None, int | None, str | None]:
    value = str(text or "")
    for grammar, pattern in (("pass", _PASSER), ("sack", _SACKED), ("spike", _SPIKE)):
        match = pattern.search(value)
        if not match: continue
        jersey = None
        if match.group("jersey"):
            try: jersey = int(match.group("jersey"))
            except (TypeError, ValueError): jersey = None
        return match.group("name").strip(), jersey, grammar
    return None, None, None


def _name_parts(value: str) -> list[str]: return normalize_alias(value).split()


def _resolve_qb(token: str | None, jersey: int | None, qbs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if not qbs: return None, "no_qb_roster"
    if jersey is not None:
        jersey_matches = []
        for qb in qbs:
            try: qb_jersey = int(qb.get("jersey")) if qb.get("jersey") is not None else None
            except (TypeError, ValueError): qb_jersey = None
            if qb_jersey == jersey: jersey_matches.append(qb)
        if len(jersey_matches) == 1: return jersey_matches[0], "jersey"
        if len(jersey_matches) > 1: qbs = jersey_matches
    if not token: return None, "no_passer_token"
    parts = _name_parts(token)
    if not parts: return None, "no_passer_token"
    token_last = parts[-1]; token_first = parts[0] if len(parts) > 1 else ""; normalized_token = normalize_alias(token)
    matches = []; exact_matches = []
    for qb in qbs:
        first = normalize_alias(str(qb.get("first_name") or "")); last = normalize_alias(str(qb.get("last_name") or "")); full = normalize_alias(f"{first} {last}")
        if normalized_token == full: exact_matches.append(qb); continue
        last_match = token_last == last
        initial_match = bool(token_first and first and token_first[0] == first[0])
        first_match = bool(token_first and first and (token_first == first or initial_match))
        if last_match and (not token_first or first_match): matches.append(qb)
    if len(exact_matches) == 1: return exact_matches[0], "exact_name"
    if len(exact_matches) > 1: return None, "ambiguous"
    if len(matches) == 1: return matches[0], "initial_last"
    if len(matches) > 1: return None, "ambiguous"
    return None, "unresolved_name"


def build(repository, *, from_season: int = 2025, to_season: int | None = None,
          parser_version: str = PARSER_VERSION, model_version: str = MODEL_VERSION,
          metric_version: str = METRIC_VERSION) -> dict[str, Any]:
    initialize(repository); to_season = int(to_season if to_season is not None else from_season); from_season = int(from_season)
    now = datetime.now(timezone.utc).isoformat()
    with closing(repository._connect()) as connection:
        roster_rows = [dict(r) for r in connection.execute("""SELECT player_id,season,team,first_name,last_name,position,jersey FROM players WHERE season BETWEEN ? AND ? AND UPPER(COALESCE(position,''))='QB'""", (from_season, to_season)).fetchall()]
    qbs_by_team = defaultdict(list)
    for row in roster_rows: qbs_by_team[(int(row["season"]), str(row["team"]))].append(row)
    aggregates = {}; unmatched_pass_plays = 0; attributed_pass_plays = 0; audit = defaultdict(int); methods = defaultdict(int); grammars = defaultdict(int)
    with closing(repository._connect()) as connection:
        cursor = connection.execute("""
          SELECT p.game_id,p.season,p.offense AS team,p.defense AS opponent,p.play_text,d.air_yards,d.yards_after_catch,d.pass_depth,e.epa
          FROM cfb_plays p JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
          JOIN cfb_play_detail d ON d.play_id=p.play_id AND d.parser_version=?
          JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=?
          WHERE p.season BETWEEN ? AND ? AND m.rush_pass='pass'
          ORDER BY p.season,p.game_id,p.drive_number,p.play_number
        """, (parser_version, model_version, from_season, to_season))
        for row in cursor:
            team = str(row["team"]); token, jersey, grammar = _passer_identity(row["play_text"])
            if grammar: grammars[grammar] += 1
            if token is None and jersey is None: unmatched_pass_plays += 1; audit["no_passer_token"] += 1; continue
            qb, method = _resolve_qb(token, jersey, qbs_by_team.get((int(row["season"]), team), []))
            if qb is None: unmatched_pass_plays += 1; audit[method] += 1; continue
            methods[method] += 1; attributed_pass_plays += 1
            name = f"{qb.get('first_name') or ''} {qb.get('last_name') or ''}".strip()
            key = (int(row["game_id"]), int(row["season"]), team, str(row["opponent"]), str(qb.get("player_id") or ""), name)
            item = aggregates.setdefault(key, {"attributed":0,"measured":0,"air":0.0,"yac":0.0,"epa":0.0,"behind_line":0,"short":0,"intermediate":0,"deep":0})
            item["attributed"] += 1
            if row["epa"] is not None: item["epa"] += float(row["epa"])
            if row["air_yards"] is not None:
                item["measured"] += 1; item["air"] += float(row["air_yards"])
                if row["yards_after_catch"] is not None: item["yac"] += float(row["yards_after_catch"])
            depth = str(row["pass_depth"] or "")
            if depth in {"behind_line","short","intermediate","deep"}: item[depth] += 1
    rows = []
    for (game_id, season, team, opponent, player_id, player_name), item in aggregates.items():
        attributed = int(item["attributed"]); measured = int(item["measured"])
        rows.append((game_id,season,team,opponent,player_id or None,player_name,parser_version,model_version,metric_version,attributed,measured,item["air"] if measured else None,item["air"]/measured if measured else None,item["yac"] if measured else None,item["yac"]/measured if measured else None,item["epa"] if attributed else None,item["epa"]/attributed if attributed else None,int(item["behind_line"]),int(item["short"]),int(item["intermediate"]),int(item["deep"]),measured/attributed if attributed else 0.0,now))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfb_qb_air_yards_game WHERE parser_version=? AND model_version=? AND metric_version=? AND season BETWEEN ? AND ?", (parser_version,model_version,metric_version,from_season,to_season))
        connection.executemany("""INSERT INTO cfb_qb_air_yards_game(game_id,season,team,opponent,player_id,player_name,parser_version,model_version,metric_version,attributed_pass_plays,measured_completions,measured_air_yards,measured_adot,yards_after_catch,yac_per_completion,pass_epa,epa_per_attributed_pass,behind_line_plays,short_plays,intermediate_plays,deep_plays,numeric_depth_coverage,built_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows); connection.commit()
    total_pass_plays = attributed_pass_plays + unmatched_pass_plays
    return {"metric_version":metric_version,"parser_version":parser_version,"model_version":model_version,"from_season":from_season,"to_season":to_season,"quarterback_games":len(rows),"attributed_pass_plays":attributed_pass_plays,"unmatched_pass_plays":unmatched_pass_plays,"passer_attribution_rate":round(attributed_pass_plays/max(1,total_pass_plays),4),"measured_completions":sum(int(r[10]) for r in rows),"attribution_methods":dict(sorted(methods.items())),"pass_play_grammar":dict(sorted(grammars.items())),"unmatched_reasons":dict(sorted(audit.items()))}


def game_summary(repository, game_id: int, *, parser_version: str = PARSER_VERSION,
                 model_version: str = MODEL_VERSION, metric_version: str = METRIC_VERSION) -> list[dict[str, Any]]:
    initialize(repository)
    with closing(repository._connect()) as connection:
        return [dict(r) for r in connection.execute("""SELECT * FROM cfb_qb_air_yards_game WHERE game_id=? AND parser_version=? AND model_version=? AND metric_version=? ORDER BY team,attributed_pass_plays DESC,player_name""", (int(game_id), parser_version, model_version, metric_version)).fetchall()]
