"""The postgame report's rendering helpers, below the Flask layer.

These turn the analysis dicts into the markup the box-score page splices in.
P3 changed what they choose to show; this pins that.
"""

from __future__ import annotations

from sports_aggregator.cfb.postgame_display import _factor_html, _is_unsettled_role
from sports_aggregator.cfb.postgame_tendencies_display import _dimension_html


GAME = {"away_team": "Alabama", "home_team": "Auburn"}


def _report(factors):
    return {"factors": factors, "winner": "Alabama", "loser": "Auburn"}


def test_a_factor_carries_a_magnitude_bar_tinted_by_the_team_it_favoured():
    html = _factor_html(_report([
        {"headline": "Alabama protected the football", "detail": "0 to 2",
         "winner": "Alabama", "loser": "Auburn", "score": 91, "confidence": "high"},
    ]), GAME)
    assert "--mag:91%" in html
    assert "--fac:var(--team-away)" in html  # Alabama is the away team here
    assert "evidence-mag" in html


def test_the_losing_teams_factors_are_broken_out_under_their_own_line():
    html = _factor_html(_report([
        {"headline": "Alabama won fourth down", "detail": "3/3", "winner": "Alabama",
         "loser": "Auburn", "score": 70, "confidence": "high"},
        {"headline": "Auburn controlled yardage", "detail": "411 to 280",
         "winner": "Auburn", "loser": "Alabama", "score": 60, "confidence": "high"},
    ]), GAME)
    assert "What Auburn won" in html
    assert html.index("Alabama won fourth down") < html.index("What Auburn won") < html.index("Auburn controlled yardage")
    assert "--fac:var(--team-home)" in html  # the Auburn factor's bar


def test_no_split_line_when_every_factor_favoured_the_winner():
    html = _factor_html(_report([
        {"headline": "A", "detail": "d", "winner": "Alabama", "loser": "Auburn", "score": 80},
        {"headline": "B", "detail": "d", "winner": "Alabama", "loser": "Auburn", "score": 50},
    ]), GAME)
    assert "postgame-evidence-split" not in html


def test_a_settled_starter_is_not_surfaced_as_an_observed_role():
    # observed #1 with four games of high-confidence evidence: the starter
    # started. No information.
    assert _is_unsettled_role({"observed_rank": 1, "games": 4}) is False


def test_a_committee_back_and_a_thin_new_starter_are_surfaced():
    assert _is_unsettled_role({"observed_rank": 2, "games": 3}) is True   # splitting reps
    assert _is_unsettled_role({"observed_rank": 1, "games": 2}) is True   # thin evidence


def test_tendency_splits_below_the_sample_line_collapse_to_one_row():
    rows = [
        {"value": "left", "plays": 12, "epa_per_play": 0.2, "success_rate": 0.5, "coverage": 0.9},
        {"value": "middle", "plays": 9, "epa_per_play": -0.1, "success_rate": 0.4, "coverage": 0.9},
        {"value": "right", "plays": 2, "epa_per_play": None, "success_rate": None, "coverage": 0.9},
    ]
    html = _dimension_html("rush_direction", rows)
    assert html.count('class="pg-tendency-row"') == 2          # left, middle
    assert "pg-tendency-fold" in html and "right 2" in html    # the folded line
    assert "epa" in html  # header still there


def test_when_every_split_is_thin_the_full_greyed_grid_is_kept():
    rows = [
        {"value": "left", "plays": 2, "coverage": 0.5},
        {"value": "middle", "plays": 1, "coverage": 0.5},
    ]
    html = _dimension_html("rush_direction", rows)
    assert "low-sample" in html
    assert "pg-tendency-fold" not in html
