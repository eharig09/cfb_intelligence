"""Signing classes, and how the three kinds of prior evidence compare.

The `recruits` table was synced by a bootstrap step and read by nothing. Every
consequence of that showed up on the same page: a five-star quarterback ranked
tenth in the country sat fourth on his own depth chart, behind three backups in
the bottom third of graded players, and never appeared under notable arrivals
at all because twenty transfers were listed ahead of him by category rather
than by quality.

Comparing the three kinds of evidence
-------------------------------------

A player arrives at a depth board with up to three things on his record: what he
produced, how he was graded, and what he was rated coming out of school. They
measure different things, so ranking one against another needs an explicit rule
rather than an accident of sort order. All three map to a common 0-1 scale:

* Counting production is normalised **within its category**, because the
  categories are not remotely comparable: median passing yards is 185 against a
  99th percentile of 3,711, while a defensive tackle count runs 8 to 96. A
  percentile within the category says how a player did against his own kind.

* A PFF interest score already runs roughly 0-95 across the graded population,
  and dividing by 100 tracks its percentile closely through the third quartile
  (median 54.1, p75 66.7). Beyond that it compresses slightly, which understates
  the very best graded players and is the safe direction to err.
* A recruiting rating occupies a narrow band — 0.75 at the bottom of two stars
  to 0.999 at the top of five — so it is stretched across the same 0-1 range.
* Recruiting evidence is then discounted, because it is an opinion about a
  player who has not taken a college snap while the other two describe one who
  has.

Leaving production out was not a small omission. Across twenty-five teams it
put a three-star signee above a back who ran for 788 yards, and seventy-seven
more like it, because a player without a PFF grade scored zero however much he
had actually done.

The discount is what keeps this honest in both directions. An elite recruit
outranks a seventh-percentile backup, which is the case that was visibly wrong.
A genuine starter still outranks an elite recruit, which is the case that would
be wrong in the other direction if rating simply won.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.repository import CFBRepository


#: Recruiting ratings occupy this band; two stars begin near the bottom and the
#: best five-star lands just under 1.0.
RATING_FLOOR, RATING_CEILING = 0.75, 1.0

#: How much a recruiting rating is discounted against a college grade.
#:
#: Chosen by where it puts the crossover rather than by feel. At 0.55 the best
#: recruit in the country scores about 0.54, which lands just under the median
#: graded player (interest 54.1) and well above the bottom third. So an elite
#: signee outranks a backup who barely played, and loses to anyone who has
#: actually held a job — which is how these players are usually deployed, and
#: when it is not, that is the news rather than the default.
#:
#: A weaker discount inverts the second case: at 0.9 a five-star outscored an
#: interest of 85, a player above the 90th percentile of everyone graded.
PROJECTION_DISCOUNT = 0.55

#: Class size used when scoring a signing class. Classes range from one signee
#: to several hundred in the raw feed, so a plain sum would rank by volume.
SCORED_CLASS_SIZE = 20


def rating_strength(rating: float | None) -> float:
    """A recruiting rating as a 0-1 share of the range ratings actually use."""
    if rating is None:
        return 0.0
    span = RATING_CEILING - RATING_FLOOR
    return max(0.0, min(1.0, (float(rating) - RATING_FLOOR) / span))


def grade_strength(interest_score: float | None) -> float:
    """A PFF interest score as a 0-1 share of the graded population."""
    if interest_score is None:
        return 0.0
    return max(0.0, min(1.0, float(interest_score) / 100.0))


def evidence_score(*, pff_interest: float | None,
                   recruit_rating: float | None,
                   production: float | None = None) -> float:
    """The strongest prior evidence a player has, on one scale.

    Whichever kind is strongest decides the placement; a player keeps his best
    evidence rather than an average, because averaging would penalise a
    productive starter for a thin grade, or an elite recruit for having neither.

    `production` is already a 0-1 percentile within its own category — see
    `CFBRepository.production_strength`, which owns the distributions.
    """
    demonstrated = max(grade_strength(pff_interest), float(production or 0.0))
    projected = rating_strength(recruit_rating) * PROJECTION_DISCOUNT
    return max(demonstrated, projected)


def evidence_basis(*, pff_interest: float | None,
                   recruit_rating: float | None,
                   stars: int | None,
                   production: float | None = None) -> str | None:
    """Which evidence placed a player, in words, so the board can say."""
    produced = float(production or 0.0)
    graded = grade_strength(pff_interest)
    projected = rating_strength(recruit_rating) * PROJECTION_DISCOUNT
    if max(produced, graded, projected) <= 0:
        return None
    if produced >= graded and produced >= projected:
        return "prior-season production"
    if projected > graded:
        # "signee" would be wrong for a transfer, who carries a high-school
        # rating too. The rating is what placed him either way.
        return f"{stars}-star rating" if stars else "recruiting rating"
    return "prior-season grade"


def signing_class(repository: CFBRepository, team_id: int,
                  season: int) -> dict[str, Any]:
    """One team's signing class, scored against every other class that season.

    The score is a plain sum of the ratings of the best `SCORED_CLASS_SIZE`
    signees. Capping the count is what makes it a measure of class quality
    rather than of class size; the feed contains buckets of several hundred that
    would otherwise dominate. It is this project's arithmetic, not a published
    composite, and the components are shown so it can be checked.
    """
    team = repository.get_team(team_id)
    if team is None:
        return {"season": season, "team": None, "signees": [], "counts": {},
                "points": None, "national_rank": None, "classes_ranked": 0}

    repository.initialize()
    with closing(repository._connect()) as connection:
        signees = [dict(row) for row in connection.execute(
            """SELECT recruit_id, athlete_id, name, position, stars, rating,
                      ranking, home_city, home_state, recruit_type
               FROM recruits WHERE season=? AND committed_to=?
               ORDER BY COALESCE(rating,0) DESC, name""",
            (season, team["school"]))]
        totals = [dict(row) for row in connection.execute(
            """SELECT committed_to, rating FROM recruits
               WHERE season=? AND committed_to IS NOT NULL AND rating IS NOT NULL
               ORDER BY committed_to, rating DESC""", (season,))]

    by_team: dict[str, list[float]] = {}
    for row in totals:
        by_team.setdefault(row["committed_to"], []).append(float(row["rating"]))
    scored = {name: round(sum(ratings[:SCORED_CLASS_SIZE]), 3)
              for name, ratings in by_team.items()}
    ranking = sorted(scored.items(), key=lambda pair: -pair[1])
    national_rank = next((index for index, (name, _) in enumerate(ranking, start=1)
                          if name == team["school"]), None)

    counts: dict[str, int] = {}
    for row in signees:
        key = f"{row['stars']}-star" if row.get("stars") else "unrated"
        counts[key] = counts.get(key, 0) + 1

    rated = [row["rating"] for row in signees if row.get("rating")]
    return {
        "season": season,
        "team": team["school"],
        "signees": signees,
        "counts": counts,
        "signee_count": len(signees),
        "points": scored.get(team["school"]),
        "average_rating": round(sum(rated) / len(rated), 4) if rated else None,
        "best_ranking": min((row["ranking"] for row in signees
                             if row.get("ranking")), default=None),
        "national_rank": national_rank,
        "classes_ranked": len(ranking),
        "scored_class_size": SCORED_CLASS_SIZE,
    }
