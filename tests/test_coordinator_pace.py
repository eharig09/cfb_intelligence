"""Career tempo for a coordinator, and for the offence when he is unknown."""

from __future__ import annotations

import sqlite3

import pytest

from sports_aggregator.cfb import coordinator_pace as cp
from sports_aggregator.cfb.repository import CFBRepository


@pytest.fixture()
def repository(tmp_path):
    from sports_aggregator.cfb.play_by_play import initialize as init_plays
    from sports_aggregator.cfb.coordinators import initialize as init_coordinators
    repo = CFBRepository(str(tmp_path / "cfb.sqlite3"))
    repo.initialize()
    init_plays(repo)
    init_coordinators(repo)
    return repo


def _drive(connection, *, game_id, season, offense, drive, plays, gap,
           passes=0, start=900):
    """One drive of evenly spaced snaps, `gap` seconds apart."""
    for number in range(plays):
        clock = start - number * gap
        play_id = f"{game_id}-{drive}-{number}"
        connection.execute(
            "INSERT INTO cfb_plays(play_id, game_id, season, week, offense, defense,"
            " drive_id, drive_number, play_number, period, clock_minutes, clock_seconds,"
            " raw_json, imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (play_id, game_id, season, 1, offense, "Other", f"{game_id}-{drive}",
             drive, number, 4, clock // 60, clock % 60, "{}", "2026-01-01"))
        connection.execute(
            "INSERT INTO cfb_play_metrics(play_id, metric_version, rush_pass,"
            " garbage_time, derived_at) VALUES(?,?,?,?,?)",
            (play_id, cp.METRIC_VERSION,
             "pass" if number < passes else "rush", 0, "2026-01-01"))


def _seed(repository, stops):
    with sqlite3.connect(repository.path) as connection:
        for index, (season, team, games, gap) in enumerate(stops):
            for game in range(games):
                # Enough snaps that the tempo clears MIN_INTERVALS.
                _drive(connection, game_id=1000 + index * 100 + game, season=season,
                       offense=team, drive=1, plays=12, gap=gap, passes=6)
        connection.commit()


def test_tempo_is_the_gap_to_the_previous_snap_on_the_same_drive(repository):
    _seed(repository, [(2025, "Test U", 5, 20)])
    packet = cp.team_pace(repository, "Test U", [2025])

    assert packet["seconds_per_play"] == pytest.approx(20.0)
    assert packet["games"] == 5
    assert packet["plays"] == 60
    assert packet["pass_rate"] == pytest.approx(0.5)
    assert packet["plays_per_game"] == pytest.approx(12.0)


def test_a_new_drive_does_not_inherit_the_previous_one_s_clock(repository):
    """Otherwise the opponent's possession lands inside an offence's tempo."""
    with sqlite3.connect(repository.path) as connection:
        _drive(connection, game_id=1, season=2025, offense="Test U", drive=1,
               plays=25, gap=20, start=3600)
        # A second drive starting much later in the game: the gap between the
        # last snap of drive one and the first of drive two is enormous.
        _drive(connection, game_id=1, season=2025, offense="Test U", drive=2,
               plays=25, gap=20, start=900)
        connection.commit()

    packet = cp.team_pace(repository, "Test U", [2025])
    assert packet["seconds_per_play"] == pytest.approx(20.0)


def test_a_gap_longer_than_a_minute_is_not_tempo(repository):
    """Period breaks, timeouts and reviews, which are not how fast a team plays."""
    _seed(repository, [(2025, "Test U", 4, 90)])
    packet = cp.team_pace(repository, "Test U", [2025])

    assert packet["plays"] == 48
    assert packet["seconds_per_play"] is None, "no interval qualified"


def test_thin_tempo_is_withheld_but_the_counts_are_not(repository):
    _seed(repository, [(2025, "Test U", 1, 20)])
    packet = cp.team_pace(repository, "Test U", [2025])

    assert packet["intervals"] < cp.MIN_INTERVALS
    assert packet["seconds_per_play"] is None
    assert packet["plays"] == 12 and packet["games"] == 1


def test_a_coordinator_is_measured_across_every_stop(repository):
    _seed(repository, [(2023, "Old School", 5, 30), (2025, "New School", 5, 20)])
    with sqlite3.connect(repository.path) as connection:
        for season, team in ((2023, "Old School"), (2025, "New School")):
            connection.execute(
                "INSERT INTO coordinator_seasons(season, team_id, team, side, role,"
                " coach_name, source_name, source_url, verified_official, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (season, 1, team, "offense", "OC", "A Coordinator",
                 "test", "http://example.invalid", 0, "2026-01-01"))
        connection.commit()

    packet = cp.coordinator_pace(repository, "A Coordinator", through_season=2026,
                                 recent_seasons=2)

    assert [row["team"] for row in packet["stops"]] == ["New School", "Old School"]
    assert packet["career"]["games"] == 10
    assert packet["career"]["seasons"] == 2
    # Both stops averaged; the recent window sees only the faster one.
    assert packet["career"]["seconds_per_play"] == pytest.approx(25.0)
    assert packet["recent"]["games"] == 5
    assert packet["recent"]["seconds_per_play"] == pytest.approx(20.0)


def test_an_unknown_coordinator_yields_nothing_rather_than_guessing(repository):
    assert cp.coordinator_pace(repository, "Nobody At All") is None
    assert cp.coordinator_pace(repository, "") is None


def test_a_team_with_no_plays_yields_nothing(repository):
    assert cp.team_pace(repository, "Test U", [2025]) is None
