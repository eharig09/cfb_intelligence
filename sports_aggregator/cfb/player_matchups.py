"""Player-vs-player and player-vs-unit watches inside a game.

An individual pairing is only used when the assignment is credible. Linemen
oppose linemen directly; a receiver is paired with a corner only when that corner
has a substantial man-coverage sample. Zone-heavy coverage is represented as a
player against the responsible unit, with the unit's leading members retained.

All matchup candidates are anchored to the requested current roster season. PFF
history supplies evaluation evidence, but it does not decide current team
membership: transferred-out and departed players are excluded, while inbound
transfers can carry their prior-school PFF evidence onto their current team.
"""

from __future__ import annotations

from contextlib import closing
import json
from typing import Any

from sports_aggregator.cfb.identity import readable_accent
from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.repository import CFBRepository


POSITION_PAIRINGS = (
    ("Edge vs tackle", {"ED"}, {"T"}, "the pass rush against the man asked to block it"),
    ("Interior rush vs guard", {"DI"}, {"G", "C"}, "pocket collapse from the middle"),
)

MIN_GRADE = 68.0
MIN_SKILL_USAGE = 60.0
HEAVY_MAN_SHARE = 0.60
HEAVY_MAN_SNAPS = 80.0


def _side_players(rows: list[dict[str, Any]], team_id: int,
                  positions: set[str]) -> list[dict[str, Any]]:
    return sorted(
        (row for row in rows if row["cfbd_team_id"] == team_id
         and row["position"] in positions and (row["interest_score"] or 0) >= MIN_GRADE),
        key=lambda row: -(row["interest_score"] or 0),
    )


def player_matchups(repository: CFBRepository, home_team_id: int, away_team_id: int, *,
                    pff_season: int = 2025, roster_season: int = 2026,
                    draft_year: int = 2027, limit: int = 8) -> list[dict[str, Any]]:
    """Rank credible individual and player-vs-unit watches for current rosters."""
    repository.initialize()
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT p.pff_player_id,p.player_name,p.normalized_name,p.position,
               p.interest_score,p.cfbd_player_id,t.team_id AS cfbd_team_id,
               t.school,t.color,r.class_year,r.jersey
               FROM pff_players p
               JOIN players r ON r.player_id=p.cfbd_player_id AND r.season=?
               JOIN teams t ON t.school=r.team
               WHERE p.season=? AND t.team_id IN (?,?)
               AND p.interest_score IS NOT NULL""",
            (roster_season, pff_season, home_team_id, away_team_id))]

        metrics: dict[tuple[str, str], dict[str, Any]] = {}
        pff_ids = [row["pff_player_id"] for row in rows]
        placeholders = ",".join("?" for _ in pff_ids) or "NULL"
        for metric_row in connection.execute(
            f"""SELECT pff_player_id,dataset,primary_grade,usage_count,metrics_json
                FROM pff_player_metrics WHERE season=?
                AND pff_player_id IN ({placeholders})""", (pff_season, *pff_ids)):
            item = dict(metric_row)
            try:
                item["raw"] = json.loads(item.get("metrics_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["raw"] = {}
            metrics[(item["pff_player_id"], item["dataset"])] = item
        for metric_row in connection.execute(
            f"""SELECT pff_player_id,dataset,primary_grade,usage_count,metrics_json
                FROM pff_supplemental_metrics WHERE season=?
                AND pff_player_id IN ({placeholders})""", (pff_season, *pff_ids)):
            item = dict(metric_row)
            try:
                item["raw"] = json.loads(item.get("metrics_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["raw"] = {}
            metrics[(item["pff_player_id"], item["dataset"])] = item

        board: dict[str, int] = {}
        try:
            for board_row in connection.execute(
                """SELECT normalized_name,rank,cfbd_player_id FROM draft_prospect_rankings
                   WHERE draft_year=?""", (draft_year,)):
                board[board_row["normalized_name"]] = board_row["rank"]
                if board_row["cfbd_player_id"]:
                    board[board_row["cfbd_player_id"]] = board_row["rank"]
        except Exception:
            board = {}

    def board_rank(player: dict[str, Any]) -> int | None:
        return board.get(player.get("cfbd_player_id")) or board.get(
            normalize_alias(player.get("player_name") or ""))

    def metric(player: dict[str, Any], dataset: str) -> dict[str, Any] | None:
        return metrics.get((player["pff_player_id"], dataset))

    def metric_players(team_id: int, positions: set[str], dataset: str,
                       minimum_usage: float = MIN_SKILL_USAGE) -> list[dict[str, Any]]:
        qualified = []
        for player in rows:
            if player["cfbd_team_id"] != team_id or player["position"] not in positions:
                continue
            evidence = metric(player, dataset)
            if not evidence or (evidence.get("primary_grade") or 0) < MIN_GRADE:
                continue
            if (evidence.get("usage_count") or 0) < minimum_usage:
                continue
            qualified.append({**player, "interest_score": evidence["primary_grade"],
                              "matchup_metric": evidence})
        return sorted(qualified, key=lambda item: -item["interest_score"])

    def unit(team_id: int, positions: set[str], dataset: str, label: str) -> dict[str, Any] | None:
        values = []
        for player in rows:
            if player["cfbd_team_id"] != team_id or player["position"] not in positions:
                continue
            evidence = metric(player, dataset)
            grade, usage = ((evidence or {}).get("primary_grade"),
                            (evidence or {}).get("usage_count"))
            if grade is not None and (usage or 0) > 0:
                values.append((player, float(grade), float(usage)))
        if len(values) < 2:
            return None
        total_usage = sum(usage for _, _, usage in values)
        weighted = sum(grade * usage for _, grade, usage in values) / total_usage
        members = sorted(values, key=lambda item: -(item[1] * min(item[2] / 150, 1)))[:4]
        school = members[0][0]["school"]
        return {
            "player_name": f"{school} {label}", "position": "Unit", "school": school,
            "color": members[0][0].get("color"),
            "accent": readable_accent(members[0][0].get("color")),
            "interest_score": round(weighted, 1), "cfbd_player_id": None,
            "is_unit": True, "usage": round(total_usage, 1),
            "members": [{"player_name": player["player_name"], "position": player["position"],
                         "grade": round(grade, 1), "cfbd_player_id": player.get("cfbd_player_id")}
                        for player, grade, _ in members],
        }

    def heavy_man_corners(team_id: int) -> list[dict[str, Any]]:
        qualified = []
        for player in rows:
            if player["cfbd_team_id"] != team_id or player["position"] != "CB":
                continue
            scheme, coverage = metric(player, "coverage_scheme"), metric(player, "coverage")
            if not scheme or not coverage:
                continue
            raw = scheme["raw"]
            try:
                man_snaps = float(raw.get("man_snap_counts_coverage") or 0)
                base = float(raw.get("base_snap_counts_coverage") or 0)
                share = (man_snaps / base if base else
                         float(raw.get("man_snap_counts_coverage_percent") or 0) / 100)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if (man_snaps >= HEAVY_MAN_SNAPS and share >= HEAVY_MAN_SHARE
                    and (coverage.get("primary_grade") or 0) >= MIN_GRADE):
                qualified.append({**player, "interest_score": coverage["primary_grade"],
                                  "man_share": share, "man_snaps": man_snaps})
        return sorted(qualified, key=lambda item: -(item["interest_score"] or 0))

    def score_pair(attacker: dict[str, Any], defender: dict[str, Any]) -> float:
        floor = min(attacker["interest_score"] or 0, defender["interest_score"] or 0)
        quality = max(0.0, min(1.0, (floor - MIN_GRADE) / 22))
        ranks = sum(bool(board_rank(player)) for player in (attacker, defender))
        return min(round(100 * quality * (1.0 + 0.25 * ranks), 1), 100.0)

    def score_player_unit(player: dict[str, Any], opponent: dict[str, Any]) -> float:
        player_quality = max(0.0, min(1.0, ((player["interest_score"] or 0) - 60) / 30))
        unit_quality = max(0.0, min(1.0, ((opponent["interest_score"] or 0) - 55) / 35))
        gap = min(abs((player["interest_score"] or 0) -
                      (opponent["interest_score"] or 0)) / 25, 1)
        score = (35 * player_quality + 20 * unit_quality
                 + 25 * min(player_quality, unit_quality) + 20 * gap)
        if board_rank(player):
            score += 8
        return round(min(score, 100), 1)

    def entry(label: str, why: str, attacker: dict[str, Any],
              defender: dict[str, Any]) -> dict[str, Any]:
        attack_rank = board_rank(attacker)
        defend_rank = None if defender.get("is_unit") else board_rank(defender)
        interest = (score_player_unit(attacker, defender) if defender.get("is_unit")
                    else score_pair(attacker, defender))
        reasons = [
            f"{attacker['school']} {attacker['position']} {attacker['interest_score']:.1f}",
            f"{defender['player_name']} {defender['interest_score']:.1f}", why,
        ]
        if attack_rank or defend_rank:
            names = []
            if attack_rank:
                names.append(f"{attacker['player_name']} #{attack_rank}")
            if defend_rank:
                names.append(f"{defender['player_name']} #{defend_rank}")
            reasons.append(f"{draft_year} board: {', '.join(names)}")
        clean_attacker = {key: value for key, value in attacker.items()
                          if key != "matchup_metric"}
        return {
            "label": label, "why": why, "interest": interest,
            "kind": "PLAYER_V_UNIT" if defender.get("is_unit") else "PLAYER_V_PLAYER",
            "attacker": {**clean_attacker, "accent": readable_accent(attacker.get("color")),
                         "board_rank": attack_rank},
            "defender": {**defender,
                         "accent": defender.get("accent") or readable_accent(defender.get("color")),
                         "board_rank": defend_rank},
            "prospect_count": int(bool(attack_rank)) + int(bool(defend_rank)),
            "reasons": reasons,
        }

    matchups = []
    for label, attack_positions, defend_positions, why in POSITION_PAIRINGS:
        for attack_team, defend_team in ((away_team_id, home_team_id),
                                         (home_team_id, away_team_id)):
            attackers = _side_players(rows, attack_team, attack_positions)
            defenders = _side_players(rows, defend_team, defend_positions)
            if attackers and defenders:
                matchups.append(entry(label, why, attackers[0], defenders[0]))

    for attack_team, defend_team in ((away_team_id, home_team_id),
                                     (home_team_id, away_team_id)):
        skill_specs = (
            ("Receiver vs coverage unit", {"WR"}, {"CB", "S"}, "secondary",
             "routes tested against the full coverage structure"),
            ("Tight end vs middle coverage", {"TE"}, {"LB", "S"}, "linebackers & safeties",
             "the seam, option routes, and intermediate middle"),
            ("Receiving back vs linebackers", {"HB", "FB"}, {"LB"}, "linebackers",
             "targets and yards from the backfield against underneath coverage"),
        )
        for label, positions, unit_positions, unit_label, why in skill_specs:
            attackers = metric_players(attack_team, positions, "receiving")
            if not attackers:
                continue
            attacker = attackers[0]
            raw = attacker["matchup_metric"]["raw"]
            if "back" in label.casefold():
                try:
                    if float(raw.get("targets") or 0) < 15 and float(raw.get("yards") or 0) < 150:
                        continue
                except (TypeError, ValueError):
                    continue
            defender = None
            actual_label = label
            if positions == {"WR"}:
                corners = heavy_man_corners(defend_team)
                if corners:
                    defender = corners[0]
                    actual_label = "Receiver vs heavy-man corner"
                    scheme = metric(attacker, "receiving_scheme")
                    if scheme:
                        scheme_raw = scheme["raw"]
                        try:
                            man_routes = float(scheme_raw.get("man_routes") or 0)
                            man_grade = float(scheme_raw.get("man_grades_pass_route") or 0)
                        except (TypeError, ValueError):
                            man_routes, man_grade = 0.0, 0.0
                        if man_routes >= MIN_SKILL_USAGE and man_grade >= MIN_GRADE:
                            attacker = {**attacker, "interest_score": man_grade}
                    why = (f"a true one-on-one: {defender['man_share']:.0%} man coverage "
                           f"across {defender['man_snaps']:.0f} snaps")
            if defender is None:
                defender = unit(defend_team, unit_positions, "coverage", unit_label)
            if defender is not None:
                matchups.append(entry(actual_label, why, attacker, defender))

    matchups.sort(key=lambda item: -item["interest"])
    return matchups[:limit]
