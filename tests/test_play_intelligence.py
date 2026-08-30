from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.play_by_play import (
    METRIC_VERSION, derive_week, game_advanced_summary, replace_week_plays,
)
from sports_aggregator.cfb.pregame_snapshots import capture_due, snapshots_for_game
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


class IntelligenceFixture(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle); os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams((
            Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None, "fbs",
                 "00274C", "FFCB05", (), ("Michigan",), None, None),
            Team(2, "Ohio State", "Buckeyes", "OSU", "Big Ten", None, "fbs",
                 "BB0000", "666666", (), ("Ohio State",), None, None),
        ))

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def game(self, *, completed=True, start=None):
        when = start or datetime(2026, 9, 5, 16, tzinfo=timezone.utc)
        return Game.from_cfbd({
            "id": 99, "season": 2026, "week": 1, "seasonType": "regular",
            "startDate": when.isoformat(), "startTimeTBD": False,
            "completed": completed, "neutralSite": False, "conferenceGame": True,
            "venue": "Stadium", "venueId": 1,
            "homeId": 1, "homeTeam": "Michigan", "homeConference": "Big Ten",
            "homePoints": 31 if completed else None,
            "awayId": 2, "awayTeam": "Ohio State", "awayConference": "Big Ten",
            "awayPoints": 17 if completed else None,
        })


class PlayMetricTests(IntelligenceFixture):
    def setUp(self):
        super().setUp()
        self.repository.replace_games(2026, [self.game(completed=True)])

    @staticmethod
    def plays():
        return [
            {"id":"p1","driveId":"d1","gameId":99,"driveNumber":1,"playNumber":1,
             "offense":"Michigan","defense":"Ohio State","home":"Michigan","away":"Ohio State",
             "offenseScore":0,"defenseScore":0,"period":1,"clock":{"minutes":14,"seconds":30},
             "yardline":25,"yardsToGoal":75,"down":1,"distance":10,"yardsGained":5,
             "scoring":False,"playType":"Rush","playText":"Runner rush for 5 yards","ppa":0.12},
            {"id":"p2","driveId":"d1","gameId":99,"driveNumber":1,"playNumber":2,
             "offense":"Michigan","defense":"Ohio State","home":"Michigan","away":"Ohio State",
             "offenseScore":0,"defenseScore":0,"period":1,"clock":{"minutes":13,"seconds":55},
             "yardline":30,"yardsToGoal":70,"down":2,"distance":5,"yardsGained":25,
             "scoring":False,"playType":"Pass Completion","playText":"Pass complete for 25 yards","ppa":0.8},
            {"id":"p3","driveId":"d2","gameId":99,"driveNumber":2,"playNumber":1,
             "offense":"Ohio State","defense":"Michigan","home":"Michigan","away":"Ohio State",
             "offenseScore":7,"defenseScore":35,"period":4,"clock":{"minutes":4,"seconds":10},
             "yardline":50,"yardsToGoal":50,"down":1,"distance":10,"yardsGained":15,
             "scoring":False,"playType":"Rush","playText":"Rush for 15 yards","ppa":0.3},
        ]

    def test_derives_success_explosiveness_and_excludes_garbage_from_summary(self):
        count = replace_week_plays(self.repository, self.plays(), season=2026, week=1)
        self.assertEqual(count, 3)
        packet = game_advanced_summary(self.repository, 99)
        michigan = packet["teams"]["Michigan"]
        ohio = packet["teams"]["Ohio State"]
        self.assertEqual(michigan["plays"], 2)
        self.assertAlmostEqual(michigan["success_rate"], 1.0)
        self.assertAlmostEqual(michigan["explosive_rate"], .5)
        self.assertEqual(ohio["plays"], 0, "late 28-point-margin play is garbage time")
        self.assertEqual(packet["metric_version"], METRIC_VERSION)
        self.assertEqual(packet["own_epa_status"], "reserved_for_fitted_expected_points_model")

    def test_raw_plays_survive_rederivation(self):
        replace_week_plays(self.repository, self.plays(), season=2026, week=1)
        with self.repository._connect() as connection:
            before = connection.execute("SELECT COUNT(*) FROM cfb_plays").fetchone()[0]
        derive_week(self.repository, season=2026, week=1)
        with self.repository._connect() as connection:
            after = connection.execute("SELECT COUNT(*) FROM cfb_plays").fetchone()[0]
            metrics = connection.execute(
                "SELECT COUNT(*) FROM cfb_play_metrics WHERE metric_version=?", (METRIC_VERSION,)
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(metrics, 3)


class PregameSnapshotTests(IntelligenceFixture):
    def test_snapshot_stage_is_immutable_on_repeat_refresh(self):
        now = datetime(2026, 9, 5, 4, tzinfo=timezone.utc)
        self.repository.replace_games(2026, [self.game(completed=False, start=now + timedelta(hours=12))])
        packet = {"snapshot_version":"pregame-v1","game":{"game_id":99},"market":{"consensus_spread":-3.5}}
        with patch("sports_aggregator.cfb.pregame_snapshots.build_snapshot", return_value=packet):
            first = capture_due(self.repository, season=2026, now=now)
            second = capture_due(self.repository, season=2026, now=now + timedelta(minutes=10))
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 0)
        rows = snapshots_for_game(self.repository, 99)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "T-24H")
        self.assertEqual(rows[0]["payload"]["market"]["consensus_spread"], -3.5)


if __name__ == "__main__":
    unittest.main()
