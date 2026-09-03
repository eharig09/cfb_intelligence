"""Wikitext must not be able to reach the page as a person's name.

A deployment that synced coordinators before the parser was fixed stored
`| oc_year =` as the offensive coordinator of dozens of teams. Because the same
string landed on all of them, the matchup card read it as one man holding five
jobs in 2022, and a team page showed "Eastern Michigan · | oc_year =".

The parser fix stops it being written. These are the two guards behind it: the
writer refuses one, and a database that already has them is cleaned on start,
so a deployment heals itself rather than waiting for someone to know.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from sports_aggregator.cfb.coordinators import (
    initialize, is_plausible_name, store_rows,
)
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


@pytest.fixture()
def repository(tmp_path):
    forget_initialized_schemas()
    repo = CFBRepository(str(tmp_path / "cfb.sqlite3"))
    repo.initialize()
    initialize(repo)
    with closing(sqlite3.connect(repo.path)) as connection, connection:
        connection.execute(
            "INSERT INTO teams(team_id, school, logos_json, updated_at)"
            " VALUES(?,?,?,?)", (1, "Eastern Michigan", "[]", "2026-01-01"))
    return repo


@pytest.mark.parametrize("value", [
    "| oc_year =", "| off_scheme = Up-tempo spread", "{{sortname}}",
    "[[Category:X]]", "", "   ", "2026", "x" * 61,
])
def test_markup_is_not_a_name(value):
    assert is_plausible_name(value) is False


@pytest.mark.parametrize("value", [
    "Ryan Grubb", "D. J. Durkin", "Jean-Pierre O'Neill", "Sean Gleeson",
])
def test_a_person_is(value):
    assert is_plausible_name(value) is True


def _rows(repository):
    with closing(sqlite3.connect(repository.path)) as connection:
        return [tuple(row) for row in connection.execute(
            "SELECT season, side, coach_name FROM coordinator_seasons")]


def test_the_writer_refuses_markup_rather_than_storing_it(repository):
    """A team with no coordinator on record beats a team whose coordinator is
    a line of template."""
    store_rows(repository, 2026, "http://example.invalid", [
        {"team": "Eastern Michigan", "side": "offense", "role": "OC",
         "coach_name": "| oc_year ="},
        {"team": "Eastern Michigan", "side": "defense", "role": "DC",
         "coach_name": "Tate Omli"},
    ])

    assert _rows(repository) == [(2026, "defense", "Tate Omli")]


def test_a_database_that_already_has_them_is_cleaned_on_start(repository):
    """The production case: synced before the fix, and nobody is going to know
    to go and delete the rows by hand."""
    with closing(sqlite3.connect(repository.path)) as connection, connection:
        for season, name in ((2026, "| oc_year ="),
                             (2025, "| off_scheme = Up-tempo spread"),
                             (2024, "Mike Piatkowski")):
            connection.execute(
                "INSERT INTO coordinator_seasons(season, team_id, team, side, role,"
                " coach_name, source_name, source_url, verified_official, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (season, 1, "Eastern Michigan", "offense", "OC", name,
                 "t", "http://example.invalid", 0, "2026-01-01"))

    forget_initialized_schemas()
    initialize(repository)

    assert _rows(repository) == [(2024, "offense", "Mike Piatkowski")]


def test_the_cleanup_leaves_real_coordinators_alone(repository):
    store_rows(repository, 2026, "http://example.invalid", [
        {"team": "Eastern Michigan", "side": "offense", "role": "OC",
         "coach_name": "Mike Piatkowski"},
    ])
    before = _rows(repository)

    forget_initialized_schemas()
    initialize(repository)

    assert _rows(repository) == before
