from sports_aggregator.cfb.expected_points_v2 import (
    _home_away_scores,
    _immediate_home_net,
    _same_half,
    _targets_for_game,
    state_key,
)


def _row(**overrides):
    base = {
        "play_id": "1",
        "game_id": 1,
        "drive_number": 1,
        "play_number": 1,
        "offense": "Home",
        "defense": "Away",
        "home_team": "Home",
        "away_team": "Away",
        "offense_score": 0,
        "defense_score": 0,
        "period": 1,
        "clock_minutes": 10,
        "clock_seconds": 0,
        "down": 1,
        "distance": 10,
        "yards_to_goal": 75,
        "provider_ppa": None,
        "home_points": 0,
        "away_points": 0,
    }
    base.update(overrides)
    return base


def test_score_orientation_when_away_has_ball():
    row = _row(offense="Away", defense="Home", offense_score=7, defense_score=3)
    assert _home_away_scores(row) == (3, 7)


def test_defensive_score_is_negative_for_current_offense():
    current = _row(offense_score=10, defense_score=10)
    following = _row(
        play_id="2", play_number=2,
        offense="Away", defense="Home",
        offense_score=17, defense_score=10,
    )
    # Home had possession, but Away scored seven before the next state.
    assert _immediate_home_net(current, following, None, None) == -7.0


def test_halftime_score_is_immediate_but_continuation_resets():
    q2 = _row(
        period=2, clock_minutes=0, clock_seconds=3,
        offense_score=14, defense_score=14,
        home_points=21, away_points=14,
    )
    q3 = _row(
        play_id="2", period=3, clock_minutes=15, clock_seconds=0,
        offense="Away", defense="Home",
        offense_score=14, defense_score=21,
        home_points=21, away_points=14,
    )
    assert not _same_half(q2, q3)
    assert _immediate_home_net(q2, q3, None, None) == 7.0
    targets = _targets_for_game([q2, q3])
    assert targets[0] == 7.0


def test_overtime_state_is_not_an_ep_state_but_preserves_regulation_score_delta():
    q4 = _row(
        period=4, clock_minutes=0, clock_seconds=2,
        offense_score=20, defense_score=27,
        home_points=34, away_points=31,
    )
    ot = _row(
        play_id="2", period=5, clock_minutes=0, clock_seconds=0,
        offense="Away", defense="Home",
        offense_score=27, defense_score=27,
        home_points=34, away_points=31,
    )
    assert _immediate_home_net(q4, ot, None, None) == 7.0
    assert 0 in state_key(ot)
