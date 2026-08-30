"""Conservative game-level EPA attribution for postgame player impact rows.

This is intentionally labelled *EPA on involved plays*, not additive individual
EPA. A pass can involve both the quarterback and receiver, and both can be shown
the same team-perspective play EPA. Position/side filters prevent special-team
names appended to provider descriptions from inheriting offensive touchdown EPA.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
import re
from typing import Any

from sports_aggregator.cfb.models import normalize_alias

OFFENSE_POSITIONS = {"QB", "RB", "HB", "FB", "WR", "TE"}
DEFENSE_PREFIXES = (
    "DL", "DE", "DT", "NT", "EDGE", "LB", "ILB", "OLB", "MLB",
    "DB", "CB", "S", "FS", "SS", "NB",
)
MODEL_VERSION = "ep-v2"


def _is_defense(position: str) -> bool:
    pos = position.upper().strip()
    return any(pos == prefix or pos.startswith(prefix) for prefix in DEFENSE_PREFIXES)


def _player_matcher(first: str, last: str, jersey: Any,
                    *, last_unique: bool, jersey_unique: bool):
    last_norm = normalize_alias(last)
    first_norm = normalize_alias(first)
    initial = first_norm[:1]
    patterns: list[re.Pattern[str]] = []
    if jersey_unique and jersey not in (None, ""):
        try:
            number = int(jersey)
            patterns.append(re.compile(rf"#\s*{number}\b", re.I))
        except (TypeError, ValueError):
            pass
    if last_norm:
        escaped_last = re.escape(last_norm).replace(r"\ ", r"[ .'-]*")
        if initial:
            patterns.append(re.compile(rf"\b{re.escape(initial)}\s*[.'’-]*\s*{escaped_last}\b", re.I))
        if last_unique:
            patterns.append(re.compile(rf"\b{escaped_last}\b", re.I))

    def matches(text: str) -> bool:
        normalized = normalize_alias(text)
        raw = str(text or "")
        if first_norm and last_norm and f"{first_norm} {last_norm}" in normalized:
            return True
        return any(pattern.search(raw) for pattern in patterns)

    return matches


def annotate_player_epa(repository, game: dict[str, Any], players: list[dict[str, Any]],
                        *, model_version: str = MODEL_VERSION) -> None:
    """Mutate report player rows with team-perspective involved-play EPA fields."""
    if not players:
        return
    game_id = int(game.get("game_id") or 0)
    season = int(game.get("season") or 0)
    teams = sorted({str(row.get("team") or "") for row in players if row.get("team")})
    if not game_id or not season or not teams:
        return

    with closing(repository._connect()) as connection:
        roster = [dict(r) for r in connection.execute(
            f"""SELECT player_id,team,first_name,last_name,position,jersey
                FROM players WHERE season=? AND team IN ({','.join('?' for _ in teams)})""",
            (season, *teams),
        ).fetchall()]
        plays = [dict(r) for r in connection.execute("""
            SELECT p.play_id,p.offense,p.defense,p.play_text,e.epa
            FROM cfb_plays p
            JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=?
            WHERE p.game_id=? AND e.epa IS NOT NULL
            ORDER BY p.drive_number,p.play_number
        """, (model_version, game_id)).fetchall()]

    roster_by_id = {str(r.get("player_id") or ""): r for r in roster if r.get("player_id")}
    roster_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in roster:
        roster_by_team[str(row.get("team") or "")].append(row)

    last_counts: dict[str, Counter[str]] = {}
    jersey_counts: dict[str, Counter[int]] = {}
    for team, rows in roster_by_team.items():
        last_counts[team] = Counter(normalize_alias(str(r.get("last_name") or "")) for r in rows if r.get("last_name"))
        jerseys: Counter[int] = Counter()
        for r in rows:
            try:
                if r.get("jersey") is not None:
                    jerseys[int(r["jersey"])] += 1
            except (TypeError, ValueError):
                pass
        jersey_counts[team] = jerseys

    for player in players:
        team = str(player.get("team") or "")
        roster_row = roster_by_id.get(str(player.get("player_id") or ""))
        if roster_row is None:
            target = normalize_alias(str(player.get("player") or ""))
            candidates = [
                r for r in roster_by_team.get(team, [])
                if normalize_alias(f"{r.get('first_name') or ''} {r.get('last_name') or ''}") == target
            ]
            roster_row = candidates[0] if len(candidates) == 1 else None
        if roster_row is None:
            continue

        first = str(roster_row.get("first_name") or "")
        last = str(roster_row.get("last_name") or "")
        position = str(roster_row.get("position") or "").upper()
        jersey = roster_row.get("jersey")
        last_norm = normalize_alias(last)
        try:
            jersey_i = int(jersey) if jersey is not None else None
        except (TypeError, ValueError):
            jersey_i = None
        matcher = _player_matcher(
            first, last, jersey,
            last_unique=bool(last_norm and last_counts.get(team, Counter()).get(last_norm, 0) == 1),
            jersey_unique=bool(jersey_i is not None and jersey_counts.get(team, Counter()).get(jersey_i, 0) == 1),
        )

        total = 0.0
        matched = 0
        for play in plays:
            offense = str(play.get("offense") or "")
            defense = str(play.get("defense") or "")
            if position in OFFENSE_POSITIONS:
                if offense != team:
                    continue
                perspective = 1.0
            elif _is_defense(position):
                if defense != team:
                    continue
                perspective = -1.0
            else:
                continue
            if not matcher(str(play.get("play_text") or "")):
                continue
            total += perspective * float(play["epa"])
            matched += 1

        if matched:
            player["involved_epa"] = round(total, 2)
            player["epa_plays"] = matched
            player["epa_label"] = "EPA on involved plays"
