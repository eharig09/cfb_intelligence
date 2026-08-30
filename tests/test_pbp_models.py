from sports_aggregator.cfb.expected_points import state_key as edp_state_key
from sports_aggregator.cfb.pace import _attach_snap_intervals, _detail_state, _margin, _rate_packet
from sports_aggregator.cfb.win_probability import state_key as wp_state_key


def test_pace_score_states_overlap_conceptually():
    leading_close={"offense_score":10,"defense_score":7}
    trailing_close={"offense_score":14,"defense_score":17}
    assert _margin(leading_close)==3
    assert _detail_state(leading_close)=="leading_one_score"
    assert _detail_state(trailing_close)=="trailing_one_score"


def test_same_drive_intervals_exclude_opponent_possession_gap():
    rows=[
        {"drive_id":"a","period":1,"clock_minutes":14,"clock_seconds":30,"rush_pass":"rush"},
        {"drive_id":"a","period":1,"clock_minutes":14,"clock_seconds":10,"rush_pass":"pass"},
        # New drive much later: it must not create a 5-minute tempo interval.
        {"drive_id":"b","period":1,"clock_minutes":9,"clock_seconds":0,"rush_pass":"rush"},
        {"drive_id":"b","period":1,"clock_minutes":8,"clock_seconds":35,"rush_pass":"pass"},
    ]
    _attach_snap_intervals(rows)
    packet=_rate_packet(rows)
    assert packet["tempo_intervals"]==2
    assert round(packet["seconds_per_play_clock"],1)==22.5
    assert round(packet["play_rate"],3)==round(60/22.5,3)


def test_edp_state_buckets_down_distance_and_field():
    assert edp_state_key({"down":1,"distance":10,"yards_to_goal":75})==(1,3,5)
    assert edp_state_key({"down":3,"distance":2,"yards_to_goal":9})==(3,1,1)


def test_wp_state_is_oriented_to_home_team_when_home_has_ball():
    key=wp_state_key({
        "offense":"Michigan","home_team":"Michigan","offense_score":21,"defense_score":14,
        "period":4,"clock_minutes":4,"clock_seconds":30,"yards_to_goal":35,
    })
    assert key[0]==4
    assert key[2]==1  # home leads by one possession
    assert key[3]==1  # home possession
    assert key[4]==2  # 35 yards from scoring goal


def test_wp_state_flips_scores_when_away_has_ball():
    key=wp_state_key({
        "offense":"Ohio State","home_team":"Michigan","offense_score":14,"defense_score":21,
        "period":4,"clock_minutes":4,"clock_seconds":30,"yards_to_goal":35,
    })
    assert key[2]==1  # Michigan/home still leads by seven
    assert key[3]==0  # away possession
    assert key[4]==4  # away 35 from its goal => home is 65 from its scoring goal
