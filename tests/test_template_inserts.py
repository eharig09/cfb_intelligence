"""Every display module that rewrites a shared template must keep matching it.

`coordinator_display`, the two `cfbdepth_*` modules, `player_injury_display` and
the postgame chain all install a Jinja loader that splices extra markup into
`cfb_team.html` / `cfb_game.html` / `cfb_player.html` / `cfb_box_score.html`
with `source.replace(ANCHOR, ...)`. When an anchor stops matching -- a template
reflow, a renamed variable -- the replace silently does nothing and the feature
disappears with no error. That is how the `<style>` blocks stopped applying, and
how the career-timeline injury notes went missing when the stint markup was
reformatted.

This renders the real install chain (every loader, in app order) and asserts the
call each splice is supposed to leave behind is actually present afterwards.
"""

from __future__ import annotations

import pytest

from app import create_app


#: Marker -> the injected global call (or class) that proves its splice fired.
EXPECTED_INSERTS = {
    "cfb_team.html": (
        "coordinator_team_summary(",
        "coordinator_continuity_fact(",
        'class="facts team-facts"',
        "cfbdepth_roster_facts(",
    ),
    "cfb_game.html": (
        "coordinator_matchup_balance(",
        "cfbdepth_situation_cards(",
        "cfbdepth_matchup_player_flags(",
    ),
    "cfb_player.html": (
        "cfbdepth_player_update_cards(",
        "player_injury_notes(",
    ),
    "cfb_box_score.html": (
        "postgame_analysis(",
        "postgame_tendencies(",
        "postgame_pace_and_leverage(",
        "postgame_qb_air_yards(",
    ),
}


@pytest.fixture(scope="module")
def rendered_sources():
    environment = create_app({"TESTING": True}).jinja_env
    return {name: environment.loader.get_source(environment, name)[0]
            for name in EXPECTED_INSERTS}


@pytest.mark.parametrize(
    ("template", "marker"),
    [(template, marker)
     for template, markers in EXPECTED_INSERTS.items()
     for marker in markers],
)
def test_the_splice_still_lands(rendered_sources, template, marker):
    assert marker in rendered_sources[template], (
        f"{template} no longer carries {marker!r} after the display loaders run "
        "-- an ANCHOR constant has drifted from the template it targets")
