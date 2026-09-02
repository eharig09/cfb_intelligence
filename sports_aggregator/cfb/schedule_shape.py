"""Week 0 and bye weeks, neither of which arrives in the data.

CFBD numbers the Week 0 weekend as week 1, so a team that opened in Dublin has
two games labelled week 1 a week apart and no way to tell them apart in a
column headed "Wk". And a bye is the absence of a game, so it has no row at
all: a schedule jumped from 3 to 5 and left the reader to notice the gap.

Both are derived here rather than stored, because both are properties of a
season's shape rather than facts about a game, and the shape is already in the
dates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Sequence


#: A separation this large between consecutive "week 1" dates may mean two
#: different weekends were given the same number.
WEEK_ZERO_GAP_DAYS = 3

#: ...but a gap alone cannot decide it. Week 1 itself runs Thursday to Tuesday,
#: so it contains gaps of three days of its own: 2020 ran 4, 5 and 8 September
#: with nothing between the 5th and the 8th, and 2021's genuine opening weekend
#: was separated from week 1 by exactly the same three days. What tells them
#: apart is size. An opening weekend is a handful of games -- one in 2016,
#: eleven in 2022 -- against sixty to ninety in the slate behind it, while the
#: games before a mid-week lull are most of the week. Anything up to this share
#: of the week's games, before a gap, is the opening weekend.
WEEK_ZERO_MAX_SHARE = 0.25

#: What the column shows for a game the postseason renumbered. CFBD restarts
#: its week count for bowls, so a bowl arrives as "week 1" too.
POSTSEASON_LABEL = "Bowl"


def _as_date(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def week_zero_cutoff(week_one_dates: Iterable[Any]) -> date | None:
    """The last date that belongs to week 0, or None when the season had none.

    Takes one date per week-1 game -- the duplicates matter, because the answer
    turns on how many games sit on each side of the gap, not only on where the
    gap is.
    """
    parsed = [value for value in map(_as_date, week_one_dates) if value]
    if len(parsed) < 2:
        return None
    counts: dict[date, int] = {}
    for value in parsed:
        counts[value] = counts.get(value, 0) + 1
    dates = sorted(counts)
    total = len(parsed)
    leading = 0
    for earlier, later in zip(dates, dates[1:]):
        leading += counts[earlier]
        if (later - earlier).days < WEEK_ZERO_GAP_DAYS:
            continue
        # The first real gap decides it, one way or the other: a small cluster
        # in front of it opened the season early, and a large one means the gap
        # is a quiet Sunday in the middle of week 1.
        return earlier if leading <= total * WEEK_ZERO_MAX_SHARE else None
    return None


def display_week(game: dict[str, Any], cutoff: date | None) -> Any:
    """What the schedule's week column should read for one game."""
    if str(game.get("season_type") or "regular") != "regular":
        return POSTSEASON_LABEL
    week = game.get("week")
    if week == 1 and cutoff is not None:
        start = _as_date(game.get("start_date"))
        if start is not None and start <= cutoff:
            return 0
    return week


def with_byes(games: Sequence[dict[str, Any]], cutoff: date | None) -> list[dict[str, Any]]:
    """The team's season in order, with a row for each week it did not play.

    Byes are inserted only between the first and last game of the regular
    season. A team is not on a bye before it opens or after it finishes, and
    the empty weeks around a conference title game it did not reach are not
    byes either -- they are the postseason, which keeps its own numbering.
    """
    annotated = []
    for game in games:
        entry = dict(game)
        entry["display_week"] = display_week(game, cutoff)
        entry["is_bye"] = False
        annotated.append(entry)

    regular = [entry for entry in annotated
               if isinstance(entry["display_week"], int)]
    if len(regular) < 2:
        return annotated

    played = {entry["display_week"] for entry in regular}
    first, last = min(played), max(played)
    byes = [{"display_week": week, "is_bye": True}
            for week in range(first, last + 1) if week not in played]
    if not byes:
        return annotated

    # Ordered by week among the regular season, with the postseason after it:
    # a bye has no date, so date order alone cannot place one.
    postseason = [entry for entry in annotated
                  if not isinstance(entry["display_week"], int)]
    ordered = sorted(regular + byes, key=lambda entry: entry["display_week"])
    return ordered + postseason


def season_week_zero_cutoff(repository, season: int) -> date | None:
    """The season's week-0 cutoff, read once per request.

    Which weekend was week 0 is a property of the whole season, not of one
    team: a side that opened in Dublin and then had a bye shows a single game
    labelled week 1, and nothing in its own schedule says the rest of the
    country played a week later.
    """
    from sports_aggregator.cfb.repository import _request_cached

    def build():
        repository.initialize()
        with repository._reader() as connection:
            return week_zero_cutoff(row[0] for row in connection.execute(
                "SELECT start_date FROM games "
                "WHERE season=? AND week=1 AND season_type='regular'",
                (int(season),)))

    return _request_cached(repository, f"week_zero:{int(season)}", build)
