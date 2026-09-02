"""The graded-returner mark, which replaced a table.

"Key returning production" listed graded returners with their position, their
team and their status -- and on a team page the last two are the same value on
every row, so it repeated the production table to add a single number. The
number now sits on the player it describes, and the players it named are marked
where a reader is already looking.
"""

from __future__ import annotations

import re

import pytest

from app import create_app


@pytest.fixture(scope="module")
def client():
    return create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False}).test_client()


def _page(client, team_id: int) -> str:
    response = client.get(f"/college-football/teams/{team_id}/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_the_production_table_carries_the_interest_score(client):
    assert ">PFF<" in _page(client, 194)


def test_graded_returners_are_marked_on_offence_and_defence(client):
    """Defence is the one that mattered: `_defense_production_group` rebuilds
    its rows from three categories rather than decorating the view's, so a
    signal added in the view alone reached only offensive players -- and a team
    whose graded returners are all defenders showed none at all.
    """
    body = _page(client, 84)
    assert "state-graded" in body, "no returner marked on a team that has some"


def test_only_returning_players_are_marked(client):
    """Arrived and departed already have their own colour; the mark says
    "returning and graded", which is the thing that had no signal."""
    body = _page(client, 194)
    for match in re.findall(r'class="[^"]*state-graded[^"]*"', body):
        assert "state-returning" in match, match


def test_the_replaced_table_is_gone(client):
    body = _page(client, 194)
    assert "Key returning production" not in body


def test_the_duplicated_movement_sections_are_gone(client):
    """Departures kept its table and moved ahead of production; the arrivals
    half of Roster movement and the whole of Key departures were a second view
    of a list already on the page."""
    body = _page(client, 194)
    assert "Key departures" not in body
    assert "Roster movement" not in body
    assert "<h2>Departures</h2>" in body


def test_the_roster_story_runs_in_the_order_it_is_asked(client):
    body = _page(client, 194)
    order = [heading for heading in re.findall(r"<h2>(.*?)</h2>", body, re.S)]
    order = [re.sub(r"<[^>]*>", "", heading).strip() for heading in order]
    for earlier, later in (("Departures", "Player production"),
                           ("Player production", "Key arrivals"),
                           ("Key arrivals", "Signing class")):
        assert order.index(earlier) < order.index(later), order


def test_the_facts_strip_is_one_row_whatever_the_count(client):
    """Six columns were hardcoded while the count is decided at render time:
    the team page adds staff continuity from a template loader, making seven,
    and the last one dropped onto a line of its own."""
    from pathlib import Path
    css = Path("static/cfb.css").read_text(encoding="utf-8")
    assert "grid-auto-flow: column" in css
    assert "repeat(6, minmax(0, 1fr))" not in css

    body = _page(client, 194)
    assert body.count('class="fact"') == 7
