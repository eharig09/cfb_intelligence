"""Week 0 and byes, neither of which arrives in the data.

CFBD numbers the opening weekend as week 1, so a team that started in Dublin
had two games a week apart both labelled 1. And a bye is the absence of a game,
so nothing arrives for one and a schedule ran 3, 5, 7, 9 with no explanation.
"""

from __future__ import annotations

from datetime import date

import pytest

from sports_aggregator.cfb.schedule_shape import (
    POSTSEASON_LABEL, display_week, week_zero_cutoff, with_byes,
)


# Real week-1 dates, as stored, for seasons that had an opening weekend and
# two that did not.
SEASONS = {
    2025: (["2025-08-23"] * 5 + ["2025-08-28"] * 11 + ["2025-08-30"] * 50,
           date(2025, 8, 23)),
    2026: (["2026-08-29"] * 7 + ["2026-08-30"] + ["2026-09-03"] * 6 + ["2026-09-05"] * 60,
           date(2026, 8, 30)),
    2016: (["2016-08-27"] + ["2016-09-01"] * 8 + ["2016-09-03"] * 48,
           date(2016, 8, 27)),
    # The pair that a gap alone cannot separate. 2021's opening weekend is
    # three days clear of week 1; 2020's week 1 has a three-day lull inside it
    # and no opening weekend at all. What tells them apart is how many games
    # sit in front of the gap: five of eighty-eight, against six of seven.
    2021: (["2021-08-28"] * 3 + ["2021-08-29"] * 2 + ["2021-09-01"]
           + ["2021-09-02"] * 8 + ["2021-09-03"] * 11 + ["2021-09-04"] * 50
           + ["2021-09-05"] * 13, date(2021, 8, 29)),
    2020: (["2020-09-04"] * 2 + ["2020-09-05"] * 4 + ["2020-09-08"], None),
    # No gap at all: Thursday to Tuesday, every day covered.
    2015: (["2015-09-03"] * 7 + ["2015-09-05"] * 50 + ["2015-09-08"], None),
}


@pytest.mark.parametrize("season", sorted(SEASONS))
def test_the_opening_weekend_is_found_where_there_was_one(season):
    dates, expected = SEASONS[season]
    assert week_zero_cutoff(dates) == expected


def test_a_lull_inside_week_one_is_not_an_opening_weekend():
    """Most of the week's games in front of the gap means the gap is in the
    middle of the week, not before it."""
    assert week_zero_cutoff(["2020-09-04"] * 40 + ["2020-09-08"] * 5) is None


def test_a_handful_of_games_in_front_of_the_same_gap_is_one():
    assert week_zero_cutoff(["2020-09-04"] * 3 + ["2020-09-08"] * 60) == date(2020, 9, 4)


def test_a_single_date_cannot_be_two_weekends():
    assert week_zero_cutoff(["2025-08-30"]) is None
    assert week_zero_cutoff([]) is None


def test_unparseable_dates_are_ignored_rather_than_raising():
    assert week_zero_cutoff(["", None, "not a date", "2025-08-30"]) is None


def _game(week, start, **extra):
    row = {"week": week, "start_date": start, "season_type": "regular",
           "game_id": f"{week}-{start}"}
    row.update(extra)
    return row


def test_the_opening_game_reads_zero_and_the_next_one_reads_one():
    cutoff = date(2025, 8, 23)
    assert display_week(_game(1, "2025-08-23T12:00:00Z"), cutoff) == 0
    assert display_week(_game(1, "2025-08-30T19:00:00Z"), cutoff) == 1


def test_without_an_opening_weekend_nothing_is_renumbered():
    assert display_week(_game(1, "2015-09-05T12:00:00Z"), None) == 1


def test_a_bowl_is_not_week_one_however_the_source_numbers_it():
    """The postseason restarts its count, so a December game arrives as week 1
    and sat in the schedule claiming to be the season opener."""
    bowl = _game(1, "2025-12-13T20:00:00Z", season_type="postseason")
    assert display_week(bowl, date(2025, 8, 23)) == POSTSEASON_LABEL


def test_missing_weeks_become_byes():
    games = [_game(1, "2025-08-30"), _game(2, "2025-09-06"), _game(4, "2025-09-20")]
    rows = with_byes(games, None)

    assert [row["display_week"] for row in rows] == [1, 2, 3, 4]
    assert [row["is_bye"] for row in rows] == [False, False, True, False]


def test_a_bye_is_never_invented_before_the_opener_or_after_the_finale():
    games = [_game(5, "2025-10-04"), _game(6, "2025-10-11")]
    rows = with_byes(games, None)
    assert [row["display_week"] for row in rows] == [5, 6]


def test_the_week_before_the_opener_is_not_a_bye_when_the_team_opened_in_week_zero():
    cutoff = date(2025, 8, 23)
    games = [_game(1, "2025-08-23"), _game(2, "2025-09-06")]
    rows = with_byes(games, cutoff)

    assert [row["display_week"] for row in rows] == [0, 1, 2]
    assert rows[1]["is_bye"] is True, "week 1 off after opening in week 0"


def test_the_postseason_follows_the_regular_season_and_takes_no_byes():
    games = [_game(13, "2025-11-22"), _game(14, "2025-11-29"),
             _game(1, "2025-12-13", season_type="postseason")]
    rows = with_byes(games, None)

    assert [row["display_week"] for row in rows] == [13, 14, POSTSEASON_LABEL]


def test_a_season_with_one_game_is_left_alone():
    rows = with_byes([_game(3, "2025-09-13")], None)
    assert len(rows) == 1 and rows[0]["is_bye"] is False


# ------------------------------------------------------------ on the real page

def test_the_team_page_shows_the_opening_weekend_and_the_byes():
    """Kansas State opened the 2025 season in Dublin, a week before everyone
    else, and took byes in 4, 8 and 11."""
    from app import create_app
    import re

    client = create_app({"TESTING": True,
                         "REGISTER_LEGACY_DASHBOARDS": False}).test_client()
    response = client.get("/college-football/teams/2306/?schedule_year=2025")
    if response.status_code != 200:
        pytest.skip("team 2306 is not in this database")
    body = response.get_data(as_text=True)
    if "schedule</h2>" not in body:
        pytest.skip("no stored schedule for this team")

    section = body[body.index("schedule</h2>"):]
    section = section[:section.index("</table>")]
    weeks = [re.sub(r"<[^>]+>", "", cells[0]).strip()
             for cells in (re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
                           for row in re.findall(r"<tr>(.*?)</tr>", section, re.S))
             if cells]

    assert weeks[0] == "0", "the Dublin opener arrives numbered week 1"
    assert weeks[:5] == ["0", "1", "2", "3", "4"], weeks[:5]
    assert "Bye" in section


def test_a_rivalry_badge_stays_on_its_own_fixture():
    """The decorator that adds them matched games to rows by position, which
    held only while every game produced exactly one row in order. Byes broke
    that and moved Kansas State's Sunflower Showdown badge onto TCU."""
    from app import create_app
    import re

    client = create_app({"TESTING": True,
                         "REGISTER_LEGACY_DASHBOARDS": False}).test_client()
    response = client.get("/college-football/teams/2306/?schedule_year=2025")
    if response.status_code != 200:
        pytest.skip("team 2306 is not in this database")
    body = response.get_data(as_text=True)
    if "Sunflower" not in body:
        pytest.skip("rivalry data is not loaded")

    section = body[body.index("schedule</h2>"):]
    section = section[:section.index("</table>")]
    for row in re.findall(r"<tr>(.*?)</tr>", section, re.S):
        text = re.sub(r"<[^>]+>", " ", row)
        if "Sunflower" in text:
            assert "Kansas" in text, text[:160]
        if "Farmageddon" in text:
            assert "Iowa State" in text, text[:160]
