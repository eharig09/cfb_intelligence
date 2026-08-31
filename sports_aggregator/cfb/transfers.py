"""Separate the transfers that change a season from the ones that do not.

A portal list treats a starting quarterback and a fourth-string safety as the
same event. They are not, and the difference is measurable from data already
stored: what the player actually produced, how he graded, and how highly he was
rated on the way in.

Impact is scored from evidence, in this order of confidence:

1. **Prior production** -- snaps and statistics at his last school, which is the
   only direct evidence of what he did.
2. **Prior grade** -- the PFF interest score, where his identity links.
3. **Recruiting rating** -- the portal rating CFBD publishes, which is opinion
   and is weighted least.

A transfer with no evidence is reported as unproven rather than as low impact:
those are different statements, and only one of them is supportable.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.repository import CFBRepository


#: Volume statistics that establish a player was on the field, per category.
PRODUCTION_STATS = {
    "passing": ("ATT", 250),
    "rushing": ("CAR", 120),
    "receiving": ("REC", 40),
    "defensive": ("TOT", 55),
    "kicking": ("FGA", 18),
    "punting": ("NO", 45),
}

#: Bands used to describe impact. Thresholds are on the 0-100 scored value.
IMPACT_BANDS = (
    (70, "HIGH", "Proven starter"),
    (45, "MEDIUM", "Rotational production"),
    (20, "LOW", "Limited prior role"),
    (0, "UNPROVEN", "No prior production on record"),
)


def _band(score: float, has_evidence: bool) -> tuple[str, str]:
    if not has_evidence:
        return "UNPROVEN", "No prior production on record"
    for threshold, code, label in IMPACT_BANDS:
        if score >= threshold:
            return code, label
    return "UNPROVEN", "No prior production on record"


def rank_transfers(repository: CFBRepository, *, season: int, team_id: int | None = None,
                   direction: str = "in", limit: int = 40) -> list[dict[str, Any]]:
    """Score portal entries by the evidence that they will matter."""
    repository.initialize()
    prior_season = season - 1
    with repository._reader() as connection:
        team_name = None
        if team_id is not None:
            row = connection.execute(
                "SELECT school FROM teams WHERE team_id=?", (team_id,)).fetchone()
            team_name = row["school"] if row else None
        clause, params = "", [season]
        if team_name:
            column = "destination" if direction == "in" else "origin"
            clause = f" AND {column}=?"
            params.append(team_name)
        transfers = [dict(row) for row in connection.execute(
            f"""SELECT * FROM player_transfers WHERE season=?{clause}
                ORDER BY COALESCE(rating,0) DESC""", params)]
        if not transfers:
            return []
        names = tuple({row["normalized_name"] for row in transfers})
        placeholders = ",".join("?" for _ in names)
        production: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            f"""SELECT s.player,s.team,s.category,s.stat_type,s.numeric_value,s.season
                FROM player_season_stats s
                JOIN players p ON p.player_id=s.player_id AND p.season=s.season
                WHERE s.season=? AND p.normalized_name IN ({placeholders})""",
            (prior_season, *names)
        ):
            key = row["player"]
            production.setdefault(key, []).append(dict(row))
        # Fall back to a name index when the stat rows carry no linked player.
        by_normal: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            f"""SELECT normalized_name,player_id FROM players WHERE season=?
                AND normalized_name IN ({placeholders})""", (prior_season, *names)
        ):
            by_normal.setdefault(row["normalized_name"], []).append(dict(row))
        stat_rows: dict[str, list[dict[str, Any]]] = {}
        identifiers = [row["player_id"] for rows in by_normal.values() for row in rows]
        if identifiers:
            id_placeholders = ",".join("?" for _ in identifiers)
            for row in connection.execute(
                f"""SELECT player_id,category,stat_type,numeric_value
                    FROM player_season_stats WHERE season=?
                    AND player_id IN ({id_placeholders})""", (prior_season, *identifiers)
            ):
                stat_rows.setdefault(row["player_id"], []).append(dict(row))
        grades = {row["normalized_name"]: row["interest_score"] for row in connection.execute(
            f"""SELECT normalized_name,interest_score FROM pff_players
                WHERE season=? AND normalized_name IN ({placeholders})
                AND interest_score IS NOT NULL""", (prior_season, *names))}
        # A transfer only becomes clickable once it resolves to a roster row, and
        # the destination roster is where he now is. Without this every portal
        # table rendered plain text with nowhere to go.
        destinations: dict[tuple[str, str], list[str]] = {}
        for row in connection.execute(
            f"""SELECT player_id,normalized_name,team FROM players WHERE season=?
                AND normalized_name IN ({placeholders})""", (season, *names)
        ):
            destinations.setdefault((row["normalized_name"], row["team"]), []).append(
                row["player_id"])

    ranked = []
    for transfer in transfers:
        normalized = transfer["normalized_name"]
        rows = []
        for entry in by_normal.get(normalized, []):
            rows.extend(stat_rows.get(entry["player_id"], []))
        volume_score, volume_note = 0.0, None
        for row in rows:
            spec = PRODUCTION_STATS.get(row["category"])
            if not spec or row["stat_type"] != spec[0]:
                continue
            share = min(1.0, (row["numeric_value"] or 0) / spec[1])
            if share > volume_score:
                volume_score = share
                volume_note = (f"{row['numeric_value']:g} {spec[0]} "
                               f"in {prior_season} {row['category']}")
        grade = grades.get(normalized)
        grade_score = max(0.0, min(1.0, ((grade or 0) - 60) / 30)) if grade else 0.0
        rating = transfer.get("rating") or 0
        rating_score = max(0.0, min(1.0, (rating - 0.75) / 0.25)) if rating else 0.0

        has_evidence = bool(volume_note or grade)
        # Production dominates; recruiting rating is opinion and is weighted least.
        score = round(100 * (0.6 * volume_score + 0.3 * grade_score + 0.1 * rating_score), 1)
        band, label = _band(score, has_evidence)
        reasons = []
        if volume_note:
            reasons.append(volume_note)
        if grade:
            reasons.append(f"{prior_season} PFF interest {grade:.1f}")
        if rating:
            reasons.append(f"portal rating {rating:.2f}")
        if not has_evidence:
            # A recruiting rating is opinion, not evidence of production, so the
            # absence of a record is stated even when a rating is present.
            reasons.append("no prior-season production or grade is linked")
        matches = destinations.get((normalized, transfer.get("destination") or ""), [])
        ranked.append({
            **transfer,
            "player_id": matches[0] if len(matches) == 1 else None,
            "player_name": f"{transfer['first_name']} {transfer['last_name']}",
            "impact_score": score,
            "impact_band": band,
            "impact_label": label,
            "has_evidence": has_evidence,
            "reasons": reasons,
        })
    ranked.sort(key=lambda item: (-item["impact_score"], item["player_name"]))
    return ranked[:limit]


def notable_transfers(repository: CFBRepository, *, season: int, team_id: int | None = None,
                      direction: str = "in", limit: int = 10) -> list[dict[str, Any]]:
    """Only the transfers with evidence behind them."""
    return [row for row in rank_transfers(repository, season=season, team_id=team_id,
                                          direction=direction, limit=200)
            if row["has_evidence"]][:limit]
