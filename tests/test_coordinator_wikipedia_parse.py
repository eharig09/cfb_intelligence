"""Reading a coordinator's name out of a season infobox.

Every case here was found in the real backfill. The first two put template
text into the database as a person's name -- `| oc_year =` was the most
prolific "offensive coordinator" in college football, with 38 stops.
"""

from __future__ import annotations

import pytest

from sports_aggregator.cfb.coordinator_wikipedia import coach_name, parse_coordinators


def _sides(text):
    parsed = parse_coordinators(text)
    return {"offense": parsed["offense"], "defense": parsed["defense"]}


def test_an_empty_field_does_not_capture_the_next_line():
    r"""`\s` matches newlines, so `\s*=\s*` walked off the end of its own line
    and an empty field took the following infobox line as its value. It also
    must not: an empty `off_coach` under a head coach falls back to the head
    coach (the play-caller), never to `off_scheme`."""
    text = ("| head_coach = Joe Moorhead\n"
            "| off_coach =\n"
            "| off_scheme = Up-tempo spread\n"
            "| def_coach = Someone\n")
    parsed = parse_coordinators(text)
    assert parsed["offense"] == "Joe Moorhead"
    assert parsed["offense_via"] == "head_coach"
    assert (parsed["defense"], parsed["defense_via"]) == ("Someone", "coordinator")


def test_an_empty_coordinator_field_with_no_head_coach_stays_empty():
    text = "| off_coach = \n| oc_year =\n| def_coach = Real Name\n"
    parsed = parse_coordinators(text)
    assert parsed["offense"] is None and parsed["offense_via"] is None


def test_the_head_coach_fills_both_blank_sides_when_that_is_all_there_is():
    parsed = parse_coordinators("| head_coach = Jedd Fisch\n| off_coach =\n")
    assert parsed["offense"] == "Jedd Fisch" and parsed["offense_via"] == "head_coach"
    assert parsed["defense"] == "Jedd Fisch" and parsed["defense_via"] == "head_coach"


def test_a_mid_season_change_yields_the_coach_who_started_the_season():
    """Both names are in the field, split by a line break. Stripping tags
    without honouring it produced "Bobby Petrino (2nd season; first 5
    games)Kolby Smith (interim; remainder of season)" as one name."""
    text = ("| off_coach = Bobby Petrino (2nd season; first 5 games)<br/>"
            "Kolby Smith (interim; remainder of season)\n| def_coach = X\n")
    assert parse_coordinators(text)["offense"] == "Bobby Petrino"


def test_a_tenure_note_is_not_part_of_the_name():
    assert parse_coordinators("| off_coach = Alex Atkins (3rd season)\n")["offense"] == "Alex Atkins"


def test_wikilinks_still_resolve_to_the_display_name():
    text = "| off_coach = [[Ryan Grubb (coach)|Ryan Grubb]]\n| def_coach = [[Kane Wommack]]\n"
    assert _sides(text) == {"offense": "Ryan Grubb", "defense": "Kane Wommack"}
    assert parse_coordinators(text)["offense_via"] == "coordinator"


def test_a_co_coordinator_alias_is_reached_when_the_first_key_is_empty():
    text = "| off_coach =\n| cooff_coach1 = Real Person\n| def_coach = Q\n"
    assert parse_coordinators(text)["offense"] == "Real Person"


def test_an_indented_field_still_matches():
    assert parse_coordinators("  | off_coach = Indented Name\n")["offense"] == "Indented Name"


@pytest.mark.parametrize("value", [
    "| oc_year =",
    "| off_scheme = Up-tempo spread",
    "{{sortname|A|B}}",
    "[[Category:Something]]",
    "",
    "   ",
    "2015",
    "x" * 61,
])
def test_markup_and_nonsense_are_refused_rather_than_stored(value):
    """A wrong name on a matchup page is worse than no name at all."""
    assert coach_name(value) == ""


@pytest.mark.parametrize("value,expected", [
    ("Ryan Grubb", "Ryan Grubb"),
    ("Bobby Petrino", "Bobby Petrino"),
    ("Shea Patterson Jr.", "Shea Patterson Jr."),
    ("Jean-Pierre O'Neill", "Jean-Pierre O'Neill"),
])
def test_real_names_survive(value, expected):
    assert coach_name(value) == expected
