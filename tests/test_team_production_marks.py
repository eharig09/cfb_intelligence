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


def _facts_strip(body: str, css_class: str) -> str:
    """The `<div class="facts ...">` block, matched by div depth so a second
    strip further down the page cannot leak into the count."""
    open_at = body.rindex("<div", 0, body.index(f'class="{css_class}"'))
    depth, index = 0, open_at
    while index < len(body):
        if body.startswith("<div", index):
            depth += 1
        elif body.startswith("</div>", index):
            depth -= 1
            if depth == 0:
                return body[open_at:index]
        index += 1
    return body[open_at:]


def test_the_facts_strip_is_one_row_whatever_the_count(client):
    """Six columns were hardcoded while the count is decided at render time:
    the team page adds staff continuity from a template loader, making seven,
    and the last one dropped onto a line of its own."""
    from pathlib import Path
    css = Path("static/cfb.css").read_text(encoding="utf-8")
    assert "grid-auto-flow: column" in css
    assert "repeat(6, minmax(0, 1fr))" not in css

    # Scoped to the coordinator-augmented strip: the CFBDepth roster-breakdown
    # strip lower on the page also renders `class="fact"` cards when that
    # private export is loaded.
    strip = _facts_strip(_page(client, 194), "facts team-facts")
    assert strip.count('class="fact"') == 7


# --------------------------------------------------------------------- identity

def _identity_row(**overrides):
    row = {"position_group": "RB", "season": 2025, "rush_yards": 1400.0,
           "rush_yards_share": 62.5, "touchdowns": 18.0, "pff_grade": 81.2,
           "pff_detail": "rushing 81.2; blocking 60.0"}
    row.update(overrides)
    return row


def test_position_identity_shows_a_second_measure_for_the_group():
    """One number does not describe a position: a back who runs for 1,400 and
    one who runs for 1,400 and scores eighteen times are different players."""
    from sports_aggregator.cfb import views
    table = views.position_philosophy_table([_identity_row()], 2025)
    keys = [column.key for column in table.columns]
    assert "second" in keys
    assert table.rows[0]["second"] == 18.0
    assert table.rows[0]["second_sub"] == "TD"


def test_the_season_to_date_column_appears_only_once_there_is_a_season():
    from sports_aggregator.cfb import views
    without = views.position_philosophy_table([_identity_row()], 2025)
    assert "to_date" not in [column.key for column in without.columns]

    with_season = views.position_philosophy_table(
        [_identity_row()], 2025, current=[], current_season=2026)
    assert "to_date" in [column.key for column in with_season.columns]
    assert with_season.rows[0]["to_date"] is None, "nothing played yet"


def test_season_to_date_is_reported_against_last_year_not_as_a_bare_number():
    """A partial-season total compares to nothing on its own. What it is worth
    knowing is how far through last year's figure the group already is."""
    from sports_aggregator.cfb import views
    table = views.position_philosophy_table(
        [_identity_row()], 2025,
        current=[_identity_row(season=2026, rush_yards=700.0)],
        current_season=2026)

    assert table.rows[0]["to_date"] == 700.0
    assert table.rows[0]["to_date_sub"] == "50% of 2025"


def test_a_group_with_no_individual_statistic_still_lists_its_grade():
    from sports_aggregator.cfb import views
    table = views.position_philosophy_table(
        [_identity_row(position_group="OL", pff_grade=69.2,
                       pff_detail="blocking 69.2")],
        2025, current=[], current_season=2026)

    row = table.rows[0]
    assert row["production"] is None and row["second"] is None
    assert row["to_date"] is None, "no metric means nothing to compare"
    assert row["pff_grade"] == 69.2
