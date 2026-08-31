"""How much of a graded unit is still here, and how to age last year's grade.

A team page in August shows PFF unit grades from last season. Those grades are
real, but they describe a unit that has partly graduated, transferred out, and
been replaced. An offensive line that graded 78.0 with four starters gone is not
a 78.0 offensive line, and presenting it as one is the same mistake
:mod:`sports_aggregator.cfb.roster_production` exists to prevent for counting
statistics.

This module answers the same question for graded units, by the same rule: place
every graded player in one of returning, departed, or arrived, and never merge
production earned at another school into this team's total.

Continuity is measured in **usage**, not headcount, because that is how the unit
grade itself is built. ``pff_position_groups.weighted_grade`` weights each
player by snaps, so a unit that returns its four highest-snap linemen and loses
three reserves has returned most of what produced the grade even though it lost
more bodies than it kept.

Two honesty constraints shape the numbers here:

* Continuity is decided against **this** season's roster, never against PFF's
  stored ``cfbd_player_id``. That link is written when the export is imported,
  by matching PFF's name against the roster of *that* season — so a player who
  has since left is ``unresolved``, and treating unresolved snaps as unknown
  puts every departure into the "cannot tell" bucket. The effect is not a small
  bias but an inversion: Michigan's 2025 edge unit lost its top five players by
  snap count, every one of them unresolved, and a share taken over linked
  players alone reports that unit as fully returning when about 5% of its snaps
  came back. A graded player is returning if he is on the current roster by id
  or by normalized name, and departed otherwise. Every graded snap is
  classified, so the denominator is the whole unit.
* Arrived players carry a grade earned somewhere else against other opponents.
  It is reported beside the unit, never added into it.

Blending
--------

Once the season starts there are two grades for the same unit and neither is
sufficient alone: last year's is stale, this year's is thin. The blend is
credibility weighting — current evidence accumulates, and the prior's weight is
scaled by how much of that prior unit is actually still on the field:

    prior_weight   = PRIOR_CREDIBILITY_GAMES * returning_share
    blended        = (games * current + prior_weight * prior)
                     / (games + prior_weight)

The behaviour this produces is the point:

* No current games: the blend is last season's grade exactly. That is the
  preseason page as it stands today.
* A unit that returned nobody: the prior carries no weight at all, because last
  year's grade is evidence about players who have left.
* Late in a season: current data dominates but never fully erases the prior, and
  what remains is proportional to how much of the unit carried over — which is
  the residual the grade should keep.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.models import normalize_person_name
from sports_aggregator.cfb.pff import DATASET_POSITION_GROUPS, _position_group
from sports_aggregator.cfb.repository import CFBRepository


#: States, mirroring :mod:`sports_aggregator.cfb.roster_production`.
RETURNING, DEPARTED, ARRIVED = "RETURNING", "DEPARTED", "ARRIVED"

#: Games of current-season play at which a fully-returning unit's prior grade
#: and its current grade carry equal weight.
#:
#: Four is chosen to match how the season actually reads: through three or four
#: weeks a unit has faced a narrow and often unrepresentative slate, so current
#: data should inform the grade without owning it. A unit returning half its
#: snaps reaches parity in two games instead, which is correct — there is less
#: of last year's unit left to speak for.
PRIOR_CREDIBILITY_GAMES = 4.0

#: Returning snaps established by name alone are weaker evidence than an id
#: link. Above this fraction the unit is flagged so a reader can discount it.
MAX_NAME_ONLY_SHARE = 0.5


def _blank_unit(dataset: str, position_group: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "position_group": position_group,
        "returning_usage": 0.0,
        "departed_usage": 0.0,
        "arrived_usage": 0.0,
        "name_only_usage": 0.0,
        "returning_players": 0,
        "departed_players": 0,
        "arrived_players": 0,
        "returning_leaders": [],
        "departed_leaders": [],
    }


def unit_continuity(repository: CFBRepository, team_id: int, *,
                    prior_season: int, current_season: int,
                    leaders: int = 3) -> dict[tuple[str, str], dict[str, Any]]:
    """Usage returning, departed, unresolved and arrived, per graded unit.

    Keyed by ``(dataset, position_group)`` so it lines up exactly with the rows
    in ``pff_position_groups`` that produce the displayed grade.
    """
    team = repository.get_team(team_id)
    if team is None:
        return {}

    with repository._reader() as connection:
        current = [dict(row) for row in connection.execute(
            "SELECT player_id,first_name,last_name FROM players WHERE season=? AND team=?",
            (current_season, team["school"]))]
        roster = {row["player_id"] for row in current if row["player_id"]}
        roster_names = {
            normalize_person_name(f"{row['first_name']} {row['last_name']}")
            for row in current}
        roster_names.discard("")

        prior_rows = [dict(row) for row in connection.execute(
            """SELECT p.pff_player_id, p.player_name, p.position, p.cfbd_player_id,
                      p.normalized_name, m.dataset, m.usage_count, m.primary_grade
               FROM pff_players p
               JOIN pff_player_metrics m
                 ON m.season = p.season AND m.pff_player_id = p.pff_player_id
               WHERE p.season = ? AND p.cfbd_team_id = ? AND m.usage_count > 0""",
            (prior_season, team_id))]

        # Grades earned elsewhere last season by players now on THIS roster.
        # Without the roster restriction this counts every graded transfer in
        # the country rather than the ones who arrived here.
        arrived_rows: list[dict[str, Any]] = []
        if roster:
            placeholders = ",".join("?" for _ in roster)
            arrived_rows = [dict(row) for row in connection.execute(
                f"""SELECT p.player_name, p.position, p.cfbd_team, m.dataset,
                           m.usage_count, m.primary_grade
                    FROM pff_players p
                    JOIN pff_player_metrics m
                      ON m.season = p.season AND m.pff_player_id = p.pff_player_id
                    WHERE p.season = ?
                      AND (p.cfbd_team_id IS NULL OR p.cfbd_team_id != ?)
                      AND p.cfbd_player_id IN ({placeholders})
                      AND m.usage_count > 0""",
                (prior_season, team_id, *roster))]

    units: dict[tuple[str, str], dict[str, Any]] = {}

    def bucket(dataset: str, position: str) -> tuple[str, str] | None:
        group = _position_group(str(position or ""))
        allowed = DATASET_POSITION_GROUPS.get(dataset)
        if not allowed or group not in allowed:
            return None
        return (dataset, group)

    for row in prior_rows:
        key = bucket(row["dataset"], row["position"])
        if key is None:
            continue
        entry = units.setdefault(key, _blank_unit(*key))
        usage = float(row["usage_count"] or 0)
        identifier = row["cfbd_player_id"]
        by_id = bool(identifier) and identifier in roster
        by_name = (not by_id) and row["normalized_name"] in roster_names
        state = RETURNING if (by_id or by_name) else DEPARTED
        if by_name:
            # Weaker evidence than an id link; surfaced so it can be discounted.
            entry["name_only_usage"] += usage
        entry[f"{state.lower()}_usage"] += usage
        entry[f"{state.lower()}_players"] += 1
        entry[f"{state.lower()}_leaders"].append({
            "player": row["player_name"], "position": row["position"],
            "usage": usage, "grade": row["primary_grade"],
            "matched_by": "id" if by_id else ("name" if by_name else None),
        })

    # Arrived players are matched to this team's current roster, then placed in
    # the unit they would join. Their usage never enters the returning share.
    for row in arrived_rows:
        key = bucket(row["dataset"], row["position"])
        if key is None:
            continue
        entry = units.setdefault(key, _blank_unit(*key))
        entry["arrived_usage"] += float(row["usage_count"] or 0)
        entry["arrived_players"] += 1

    for entry in units.values():
        # Every graded snap is either on this year's roster or it is not, so the
        # denominator is the whole unit rather than the resolvable part of it.
        total = entry["returning_usage"] + entry["departed_usage"]
        entry["total_usage"] = round(total, 1)
        entry["returning_share"] = (round(entry["returning_usage"] / total, 4)
                                    if total > 0 else None)
        name_only = (entry["name_only_usage"] / entry["returning_usage"]
                     if entry["returning_usage"] > 0 else 0.0)
        entry["name_only_share"] = round(name_only, 4)
        entry["strongly_matched"] = name_only <= MAX_NAME_ONLY_SHARE
        for state in ("returning", "departed"):
            entry[f"{state}_leaders"] = sorted(
                entry[f"{state}_leaders"], key=lambda item: -item["usage"])[:leaders]
        for field in ("returning_usage", "departed_usage",
                      "name_only_usage", "arrived_usage"):
            entry[field] = round(entry[field], 1)
    return units


def blend_unit_grade(*, prior_grade: float | None, returning_share: float | None,
                     current_grade: float | None = None,
                     current_games: float = 0.0) -> dict[str, Any]:
    """Weight last season's unit grade against this season's by credibility.

    Returns the blended value plus the weights and a plain-language basis, so a
    page can always say which season the number came from rather than showing a
    figure whose provenance is invisible.
    """
    share = 0.0 if returning_share is None else max(0.0, min(1.0, returning_share))
    games = max(0.0, float(current_games or 0.0))
    prior_weight = PRIOR_CREDIBILITY_GAMES * share if prior_grade is not None else 0.0
    current_weight = games if current_grade is not None else 0.0
    total = prior_weight + current_weight

    if total <= 0:
        # Nothing can be said: either there is no grade at all, or the only
        # grade available describes players who have all left.
        value = current_grade if current_grade is not None else None
        if value is None and prior_grade is not None and returning_share is None:
            # Continuity could not be measured; the prior is all there is, and
            # the caller is told exactly that.
            return {"value": round(prior_grade, 2), "prior_weight": 1.0,
                    "current_weight": 0.0, "returning_share": None,
                    "basis": "prior-season grade; continuity could not be measured"}
        return {"value": None if value is None else round(value, 2),
                "prior_weight": 0.0, "current_weight": 1.0 if value is not None else 0.0,
                "returning_share": share,
                "basis": ("current-season grade only; none of the graded unit returned"
                          if value is not None else "no grade available")}

    blended = ((prior_weight * (prior_grade or 0.0)) +
               (current_weight * (current_grade or 0.0))) / total
    prior_fraction = prior_weight / total
    return {
        "value": round(blended, 2),
        "prior_weight": round(prior_fraction, 3),
        "current_weight": round(current_weight / total, 3),
        "returning_share": share,
        "basis": _basis(prior_fraction, games, share),
    }


def _basis(prior_fraction: float, games: float, share: float) -> str:
    """Which season the number came from. Deliberately silent about the
    returning share: it sits in its own sortable column beside this, and
    repeating it there filled every row with a figure already on screen."""
    del share
    if games <= 0:
        return "prior season"
    if prior_fraction >= 0.5:
        return (f"mostly prior season ({round(prior_fraction * 100)}%) "
                f"after {games:g} games")
    return (f"mostly this season ({round((1 - prior_fraction) * 100)}%) "
            f"after {games:g} games")


def units_with_continuity(repository: CFBRepository, team_id: int, *,
                          prior_season: int, current_season: int,
                          current_grades: dict[tuple[str, str], dict[str, Any]] | None = None,
                          current_games: float = 0.0) -> list[dict[str, Any]]:
    """Prior unit grades, their continuity, and the blended figure, per unit.

    `current_grades` is keyed the same way as `unit_continuity`; it is empty
    until a season's own PFF export exists, which is the state every preseason
    page is in.
    """
    continuity = unit_continuity(repository, team_id,
                                 prior_season=prior_season,
                                 current_season=current_season)
    current_grades = current_grades or {}

    with closing(repository._connect()) as connection:
        prior = [dict(row) for row in connection.execute(
            """SELECT position_group, dataset, weighted_grade, player_count, usage_count
               FROM pff_position_groups
               WHERE season = ? AND cfbd_team_id = ? AND weighted_grade IS NOT NULL
               ORDER BY dataset, position_group""",
            (prior_season, team_id))]

    rows = []
    for group in prior:
        key = (group["dataset"], group["position_group"])
        carry = continuity.get(key, {})
        current = current_grades.get(key) or {}
        blended = blend_unit_grade(
            prior_grade=group["weighted_grade"],
            returning_share=carry.get("returning_share"),
            current_grade=current.get("weighted_grade"),
            current_games=current_games)
        rows.append({
            **group,
            "prior_season": prior_season,
            "prior_grade": group["weighted_grade"],
            "current_grade": current.get("weighted_grade"),
            "returning_share": carry.get("returning_share"),
            "returning_usage": carry.get("returning_usage"),
            "departed_usage": carry.get("departed_usage"),
            "arrived_usage": carry.get("arrived_usage"),
            "arrived_players": carry.get("arrived_players", 0),
            "returning_players": carry.get("returning_players", 0),
            "departed_players": carry.get("departed_players", 0),
            # Snaps that produced last season's grade, as classified here. This
            # is not `usage_count` from the group row: that is PFF's own total,
            # while this is the part this module could place on a roster.
            "continuity_usage": carry.get("total_usage"),
            "name_only_share": carry.get("name_only_share"),
            "strongly_matched": carry.get("strongly_matched", True),
            "returning_leaders": carry.get("returning_leaders") or [],
            "departed_leaders": carry.get("departed_leaders") or [],
            "blended_grade": blended["value"],
            "blend_basis": blended["basis"],
            "prior_weight": blended["prior_weight"],
            "current_weight": blended["current_weight"],
        })
    return rows
