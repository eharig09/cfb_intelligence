"""Read-only audit of stored CFBD play descriptions.

Measures whether play_text is present and whether common football detail phrases are
consistent enough to justify a versioned parsing/enrichment layer. The audit is
streamed from SQLite and keeps only counters and a small normalized-pattern sample.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import re
from typing import Any


_TOKEN_PATTERNS = {
    "left": re.compile(r"\bleft\b", re.I),
    "middle": re.compile(r"\b(?:middle|center)\b", re.I),
    "right": re.compile(r"\bright\b", re.I),
    "left_end": re.compile(r"\bleft\s+end\b", re.I),
    "left_tackle": re.compile(r"\bleft\s+tackle\b", re.I),
    "left_guard": re.compile(r"\bleft\s+guard\b", re.I),
    "middle_gap": re.compile(r"\b(?:middle|up the middle|center)\b", re.I),
    "right_guard": re.compile(r"\bright\s+guard\b", re.I),
    "right_tackle": re.compile(r"\bright\s+tackle\b", re.I),
    "right_end": re.compile(r"\bright\s+end\b", re.I),
    "short": re.compile(r"\bshort\b", re.I),
    "deep": re.compile(r"\bdeep\b", re.I),
    "screen": re.compile(r"\bscreen\b", re.I),
    "scramble": re.compile(r"\bscrambl(?:e|ed|ing)\b", re.I),
    "play_action": re.compile(r"\bplay[- ]action\b", re.I),
}


def _pct(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def _normalize(text: str) -> str:
    """Reduce names/numbers/yard markers so provider phrasing families surface."""
    value = text.casefold()
    value = re.sub(r"#\d+", "#N", value)
    value = re.sub(r"\b\d{1,3}\b", "N", value)
    value = re.sub(r"\b(?:at|to)\s+[a-z]{2,6}\d{1,2}\b", "at FIELD", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:220]


def audit(repository, *, from_season: int | None = None,
          to_season: int | None = None, sample_patterns: int = 20) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if from_season is not None:
        clauses.append("season>=?")
        params.append(int(from_season))
    if to_season is not None:
        clauses.append("season<=?")
        params.append(int(to_season))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    overall = Counter()
    by_season: dict[int, Counter] = defaultdict(Counter)
    by_play_type: dict[str, Counter] = defaultdict(Counter)
    phrases = Counter()
    normalized = Counter()

    with closing(repository._connect()) as connection:
        cursor = connection.execute(
            f"SELECT season,play_type,play_text FROM cfb_plays {where} ORDER BY season,game_id,drive_number,play_number",
            params,
        )
        for row in cursor:
            season = int(row["season"] or 0)
            play_type = str(row["play_type"] or "unknown")
            text = str(row["play_text"] or "").strip()
            present = bool(text)
            for counter in (overall, by_season[season], by_play_type[play_type]):
                counter["plays"] += 1
                counter["with_text"] += int(present)
            if not present:
                continue
            overall["text_chars"] += len(text)
            by_season[season]["text_chars"] += len(text)
            by_play_type[play_type]["text_chars"] += len(text)
            normalized[_normalize(text)] += 1
            for name, pattern in _TOKEN_PATTERNS.items():
                if pattern.search(text):
                    phrases[name] += 1

    def packet(counter: Counter) -> dict[str, Any]:
        plays = int(counter["plays"])
        with_text = int(counter["with_text"])
        return {
            "plays": plays,
            "with_text": with_text,
            "coverage": _pct(with_text, plays),
            "avg_text_chars": round(counter["text_chars"] / with_text, 1) if with_text else 0.0,
        }

    with_text = int(overall["with_text"])
    return {
        "from_season": from_season,
        "to_season": to_season,
        "overall": packet(overall),
        "by_season": {str(k): packet(v) for k, v in sorted(by_season.items())},
        "by_play_type": {
            key: packet(value)
            for key, value in sorted(by_play_type.items(), key=lambda item: (-item[1]["plays"], item[0]))
        },
        "phrase_coverage_among_text_plays": {
            key: {"plays": int(value), "share": _pct(int(value), with_text)}
            for key, value in sorted(phrases.items())
        },
        "common_normalized_patterns": [
            {"pattern": pattern, "plays": int(count), "share_of_text": _pct(int(count), with_text)}
            for pattern, count in normalized.most_common(max(1, int(sample_patterns)))
        ],
        "interpretation_note": (
            "Phrase counts overlap. Direction/depth fields should only be parsed on relevant rush/pass plays; "
            "this audit measures raw text consistency before a parser is introduced."
        ),
    }
