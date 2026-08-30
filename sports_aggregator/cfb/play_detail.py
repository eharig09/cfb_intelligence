"""Versioned lexical enrichment for CFBD play descriptions.

play-detail-v1 is intentionally conservative. It extracts only strong textual
signals from stored play_text and leaves unsupported concepts unknown rather than
guessing. Raw provider text remains authoritative and can be reparsed later.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re
from typing import Any

PARSER_VERSION = "play-detail-v1"
WRITE_BATCH = 1000

_DIRECTION = (
    ("left", re.compile(r"\bleft\b", re.I)),
    ("middle", re.compile(r"\b(?:middle|up the middle|center)\b", re.I)),
    ("right", re.compile(r"\bright\b", re.I)),
)
_DEPTH = (
    ("short", re.compile(r"\bshort\b", re.I)),
    ("deep", re.compile(r"\bdeep\b", re.I)),
)


def initialize(repository) -> None:
    from sports_aggregator.cfb.play_by_play import initialize as initialize_pbp
    initialize_pbp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_play_detail (
          play_id TEXT NOT NULL,
          parser_version TEXT NOT NULL,
          formation TEXT,
          tempo TEXT,
          rush_direction TEXT,
          pass_depth TEXT,
          pass_location TEXT,
          screen INTEGER NOT NULL DEFAULT 0,
          scramble INTEGER NOT NULL DEFAULT 0,
          sack INTEGER NOT NULL DEFAULT 0,
          play_action INTEGER NOT NULL DEFAULT 0,
          parser_confidence TEXT NOT NULL,
          parsed_at TEXT NOT NULL,
          PRIMARY KEY(play_id,parser_version),
          FOREIGN KEY(play_id) REFERENCES cfb_plays(play_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_play_detail_version
          ON cfb_play_detail(parser_version,play_id);
        """)
        connection.commit()


def _direction(text: str) -> str | None:
    hits = [name for name, pattern in _DIRECTION if pattern.search(text)]
    return hits[0] if len(hits) == 1 else None


def _depth(text: str) -> str | None:
    hits = [name for name, pattern in _DEPTH if pattern.search(text)]
    return hits[0] if len(hits) == 1 else None


def parse(play_type: Any, play_text: Any) -> dict[str, Any]:
    text = str(play_text or "").strip()
    lower = text.casefold()
    kind = str(play_type or "").casefold()

    is_pass = any(token in kind for token in ("pass", "interception", "sack")) or " pass " in f" {lower} " or "sack" in lower
    is_rush = "rush" in kind or " rush " in f" {lower} " or " run " in f" {lower} "

    formation = None
    if re.search(r"\bshotgun\b", text, re.I):
        formation = "shotgun"
    elif re.search(r"\bpistol\b", text, re.I):
        formation = "pistol"

    tempo = "no_huddle" if re.search(r"\bno[ -]?huddle\b", text, re.I) else None
    direction = _direction(text)
    pass_depth = _depth(text) if is_pass else None
    pass_location = direction if is_pass else None
    rush_direction = direction if is_rush and not is_pass else None

    screen = int(bool(re.search(r"\bscreen\b", text, re.I)))
    scramble = int(bool(re.search(r"\bscrambl(?:e|es|ed|ing)\b", text, re.I)))
    sack = int("sack" in kind or bool(re.search(r"\bsack(?:ed)?\b", text, re.I)))
    play_action = int(bool(re.search(r"\bplay[- ]action\b", text, re.I)))

    strong_fields = sum(value is not None for value in (formation, tempo, rush_direction, pass_depth, pass_location))
    strong_fields += screen + scramble + sack + play_action
    confidence = "high" if strong_fields >= 2 else ("medium" if strong_fields == 1 else "unknown")

    return {
        "formation": formation,
        "tempo": tempo,
        "rush_direction": rush_direction,
        "pass_depth": pass_depth,
        "pass_location": pass_location,
        "screen": screen,
        "scramble": scramble,
        "sack": sack,
        "play_action": play_action,
        "parser_confidence": confidence,
    }


def build(repository, *, from_season: int | None = None,
          to_season: int | None = None, parser_version: str = PARSER_VERSION) -> dict[str, Any]:
    initialize(repository)
    clauses = ["COALESCE(TRIM(p.play_text),'')<>''"]
    params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?")
        params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?")
        params.append(int(to_season))
    where = " AND ".join(clauses)
    parsed_at = datetime.now(timezone.utc).isoformat()

    totals = {
        "parsed": 0, "high_confidence": 0, "medium_confidence": 0,
        "rush_direction": 0, "pass_depth": 0, "pass_location": 0,
        "formation": 0, "tempo": 0, "screen": 0, "scramble": 0,
        "sack": 0, "play_action": 0,
    }

    with closing(repository._connect()) as connection:
        if from_season is not None or to_season is not None:
            season_parts = []
            season_params: list[Any] = [parser_version]
            if from_season is not None:
                season_parts.append("season>=?")
                season_params.append(int(from_season))
            if to_season is not None:
                season_parts.append("season<=?")
                season_params.append(int(to_season))
            connection.execute(
                f"DELETE FROM cfb_play_detail WHERE parser_version=? AND play_id IN (SELECT play_id FROM cfb_plays WHERE {' AND '.join(season_parts)})",
                season_params,
            )
        else:
            connection.execute("DELETE FROM cfb_play_detail WHERE parser_version=?", (parser_version,))
        connection.commit()

    batch: list[tuple[Any, ...]] = []
    with closing(repository._connect()) as reader:
        cursor = reader.execute(
            f"SELECT p.play_id,p.play_type,p.play_text FROM cfb_plays p WHERE {where} ORDER BY p.season,p.game_id,p.drive_number,p.play_number",
            params,
        )
        for row in cursor:
            detail = parse(row["play_type"], row["play_text"])
            totals["parsed"] += 1
            if detail["parser_confidence"] == "high": totals["high_confidence"] += 1
            if detail["parser_confidence"] == "medium": totals["medium_confidence"] += 1
            for key in ("rush_direction", "pass_depth", "pass_location", "formation", "tempo"):
                totals[key] += int(detail[key] is not None)
            for key in ("screen", "scramble", "sack", "play_action"):
                totals[key] += int(detail[key])
            batch.append((
                str(row["play_id"]), parser_version, detail["formation"], detail["tempo"],
                detail["rush_direction"], detail["pass_depth"], detail["pass_location"],
                detail["screen"], detail["scramble"], detail["sack"], detail["play_action"],
                detail["parser_confidence"], parsed_at,
            ))
            if len(batch) >= WRITE_BATCH:
                with closing(repository._connect()) as writer:
                    writer.executemany("""INSERT OR REPLACE INTO cfb_play_detail(
                      play_id,parser_version,formation,tempo,rush_direction,pass_depth,pass_location,
                      screen,scramble,sack,play_action,parser_confidence,parsed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
                    writer.commit()
                batch = []
    if batch:
        with closing(repository._connect()) as writer:
            writer.executemany("""INSERT OR REPLACE INTO cfb_play_detail(
              play_id,parser_version,formation,tempo,rush_direction,pass_depth,pass_location,
              screen,scramble,sack,play_action,parser_confidence,parsed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
            writer.commit()

    parsed = totals["parsed"]
    coverage = {
        key: round(totals[key] / parsed, 4) if parsed else 0.0
        for key in ("high_confidence", "medium_confidence", "rush_direction", "pass_depth", "pass_location", "formation", "tempo", "screen", "scramble", "sack", "play_action")
    }
    return {
        "parser_version": parser_version,
        "from_season": from_season,
        "to_season": to_season,
        **totals,
        "coverage_of_text_plays": coverage,
    }
