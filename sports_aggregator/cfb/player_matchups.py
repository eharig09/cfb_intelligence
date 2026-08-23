"""Individual player matchups inside a game.

Unit grades say which side of the ball decides a game. They do not say who to
watch. This pairs graded individuals across the line of scrimmage -- an edge
rusher against the tackle who has to block him, a receiver against the corner who
has to cover him -- and ranks the pairings by how good both players are.

Two players are only paired when their positions genuinely oppose each other, and
a pairing is only interesting when *both* sides grade well: a good rusher against
a poor tackle is a mismatch worth noting, but two good players is the matchup a
viewer actually tunes in for.

Draft standing is a multiplier, not the basis. A pairing of two consensus board
prospects is elevated because more people care, but a pairing of two well-graded
unranked players still surfaces.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.identity import readable_accent
from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.repository import CFBRepository


#: (label, attacking positions, defending positions, why it matters).
POSITION_PAIRINGS = (
    ("Edge vs tackle", {"ED"}, {"T"},
     "the pass rush against the man asked to block it"),
    ("Interior rush vs guard", {"DI"}, {"G", "C"},
     "pocket collapse from the middle"),
    ("Receiver vs corner", {"WR"}, {"CB"},
     "the outside passing game"),
    ("Tight end vs safety", {"TE"}, {"S", "LB"},
     "the seam and intermediate middle"),
    ("Back vs linebacker", {"HB"}, {"LB"},
     "the run game and check-downs"),
)

#: Below this PFF grade a player is not worth naming as a matchup.
MIN_GRADE = 68.0
#: Minimum snaps/usage before a grade is trusted for an individual.
MIN_USAGE = 120.0


def _side_players(rows: list[dict[str, Any]], team_id: int,
                  positions: set[str]) -> list[dict[str, Any]]:
    return sorted(
        (row for row in rows
         if row["cfbd_team_id"] == team_id and row["position"] in positions
         and (row["interest_score"] or 0) >= MIN_GRADE),
        key=lambda row: -(row["interest_score"] or 0))


def player_matchups(repository: CFBRepository, home_team_id: int, away_team_id: int, *,
                    pff_season: int = 2025, roster_season: int = 2026,
                    draft_year: int = 2027, limit: int = 8) -> list[dict[str, Any]]:
    """Rank individual matchups between graded players on the two rosters."""
    repository.initialize()
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT p.pff_player_id,p.player_name,p.normalized_name,p.position,
               p.interest_score,p.cfbd_player_id,p.cfbd_team_id,
               t.school,t.color,r.class_year,r.jersey
               FROM pff_players p
               JOIN teams t ON t.team_id=p.cfbd_team_id
               LEFT JOIN players r ON r.player_id=p.cfbd_player_id AND r.season=?
               WHERE p.season=? AND p.cfbd_team_id IN (?,?)
               AND p.interest_score IS NOT NULL""",
            (roster_season, pff_season, home_team_id, away_team_id))]
        # Only players still on a roster can play in this game.
        rows = [row for row in rows if row["cfbd_player_id"]]
        board = {}
        try:
            for entry in connection.execute(
                """SELECT normalized_name,rank,cfbd_player_id FROM draft_prospect_rankings
                   WHERE draft_year=?""", (draft_year,)):
                board[entry["normalized_name"]] = entry["rank"]
                if entry["cfbd_player_id"]:
                    board[entry["cfbd_player_id"]] = entry["rank"]
        except Exception:
            board = {}

    def board_rank(player: dict[str, Any]) -> int | None:
        return (board.get(player.get("cfbd_player_id"))
                or board.get(normalize_alias(player["player_name"])))

    matchups: list[dict[str, Any]] = []
    for label, attack_positions, defend_positions, why in POSITION_PAIRINGS:
        for attack_team, defend_team in ((away_team_id, home_team_id),
                                         (home_team_id, away_team_id)):
            attackers = _side_players(rows, attack_team, attack_positions)
            defenders = _side_players(rows, defend_team, defend_positions)
            if not attackers or not defenders:
                continue
            attacker, defender = attackers[0], defenders[0]
            attack_grade = attacker["interest_score"] or 0
            defend_grade = defender["interest_score"] or 0
            floor = min(attack_grade, defend_grade)
            quality = max(0.0, min(1.0, (floor - MIN_GRADE) / 22))

            attack_rank, defend_rank = board_rank(attacker), board_rank(defender)
            ranked = [rank for rank in (attack_rank, defend_rank) if rank]
            # Draft standing raises a pairing without being able to create one.
            draft_bonus = 1.0 + 0.25 * len(ranked)
            interest = round(100 * quality * draft_bonus, 1)
            reasons = [
                f"{attacker['school']} {attacker['position']} {attack_grade:.1f}",
                f"{defender['school']} {defender['position']} {defend_grade:.1f}",
                why,
            ]
            if ranked:
                names = []
                if attack_rank:
                    names.append(f"{attacker['player_name']} #{attack_rank}")
                if defend_rank:
                    names.append(f"{defender['player_name']} #{defend_rank}")
                reasons.append(f"{draft_year} board: {', '.join(names)}")
            matchups.append({
                "label": label,
                "why": why,
                "interest": min(interest, 100.0),
                "attacker": {
                    **attacker, "accent": readable_accent(attacker.get("color")),
                    "board_rank": attack_rank,
                },
                "defender": {
                    **defender, "accent": readable_accent(defender.get("color")),
                    "board_rank": defend_rank,
                },
                "prospect_count": len(ranked),
                "reasons": reasons,
            })
    # Sort on interest alone: the draft bonus is already folded into it, so a
    # weak pairing with one ranked player cannot outrank a strong pairing of two
    # unranked ones.
    matchups.sort(key=lambda item: -item["interest"])
    return matchups[:limit]
