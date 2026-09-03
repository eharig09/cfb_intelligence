"""The coordinator line on a team page, once there is data behind it.

`coordinator_seasons` was empty, so this rendered nothing at all and none of
these readings had ever been seen. With it populated, a first-year coordinator
led with two dashes and hid his career behind them: "— PPG · — YPG (24.0 ·
348.6)" reads as no data rather than as a full career at other schools.
"""

from __future__ import annotations

import re

import pytest

from sports_aggregator.cfb.coordinator_display import _summary_line


def _coordinator(*, program=None, career=None, role="OC", name="A Coordinator"):
    def packet(values):
        if values is None:
            return None
        return {"points_per_game": values[0], "yards_per_game": values[1]}
    return {"role": role, "coach_name": name,
            "program_performance": packet(program),
            "career_performance": packet(career)}


def _text(html_line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_line)).strip()


def test_a_record_at_this_school_leads_with_the_career_beside_it():
    line = _text(_summary_line(_coordinator(program=(29.5, 367.7), career=(33.6, 441.6))))
    assert "29.5 PPG · 367.7 YPG" in line
    assert "career 33.6 · 441.6" in line


def test_a_first_season_here_leads_with_the_career():
    """No games at the school yet, but a career at others. Leading with the
    dashes made a well-travelled coordinator look like missing data."""
    line = _text(_summary_line(_coordinator(program=None, career=(24.0, 348.6))))
    assert line.startswith("OC A Coordinator career 24.0 PPG · 348.6 YPG")
    assert "—" not in line


def test_no_record_anywhere_says_so_rather_than_claiming_a_career():
    """A coordinator arriving from outside college football. "career — PPG ·
    — YPG" asserts a career and then fails to show it."""
    line = _text(_summary_line(_coordinator(program=None, career=None)))
    assert "no stored record" in line
    assert "career" not in line


def test_a_first_stop_is_not_reported_twice():
    """His career and his time at the school are the same games."""
    line = _text(_summary_line(_coordinator(program=(9.3, 219.1), career=(9.3, 219.1))))
    assert line.count("9.3") == 1
    assert "career" not in line


def test_the_run_pass_split_rides_along_when_there_is_one():
    line = _text(_summary_line(
        _coordinator(program=(29.5, 367.7), career=(33.6, 441.6)),
        {"program": {"run_pct": 47.0, "pass_pct": 53.0}}))
    assert "R/P 47/53" in line


def test_nothing_at_all_renders_nothing():
    assert _summary_line(None) == ""
