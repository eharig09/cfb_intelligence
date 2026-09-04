"""`_week_ready` must not call a week done while a game is still missing plays.

A Thursday opener whose play-by-play publishes a day after that week's
Saturday games left the week looking finished: it had plays, they were all
derived, and `pbp backfill` skipped the re-fetch -- so Colorado at Georgia
Tech stayed blank on the site with the data sitting in CFBD.
"""

from __future__ import annotations

from datetime import timezone
import os
import tempfile
import unittest

from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.pbp_cli import _week_ready
from sports_aggregator.cfb.play_by_play import replace_week_plays
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


def _game(game_id: int, *, completed: bool):
    return Game.from_cfbd({
        "id": game_id, "season": 2026, "week": 1, "seasonType": "regular",
        "startDate": "2026-09-05T16:00:00Z", "startTimeTBD": False,
        "completed": completed, "neutralSite": False, "conferenceGame": True,
        "venue": "Stadium", "venueId": 1,
        "homeId": 1, "homeTeam": "Michigan", "homeConference": "Big Ten",
        "homePoints": 30 if completed else None,
        "awayId": 2, "awayTeam": "Ohio State", "awayConference": "Big Ten",
        "awayPoints": 20 if completed else None,
    })


def _play(play_id: str, game_id: int):
    return {"id": play_id, "driveId": f"d{game_id}", "gameId": game_id,
            "driveNumber": 1, "playNumber": 1, "offense": "Michigan",
            "defense": "Ohio State", "home": "Michigan", "away": "Ohio State",
            "offenseScore": 0, "defenseScore": 0, "period": 1,
            "clock": {"minutes": 14, "seconds": 30}, "yardline": 25,
            "yardsToGoal": 75, "down": 1, "distance": 10, "yardsGained": 6,
            "scoring": False, "playType": "Rush", "playText": "Rush for 6", "ppa": 0.1}


class WeekReadyTests(unittest.TestCase):
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

    def test_no_plays_is_not_ready(self):
        self.repository.replace_games(2026, [_game(99, completed=True)])
        self.assertEqual(_week_ready(self.repository, 2026, 1), (False, 0, 0))

    def test_every_completed_game_covered_is_ready(self):
        self.repository.replace_games(2026, [_game(99, completed=True)])
        replace_week_plays(self.repository, [_play("p1", 99)], season=2026, week=1)
        ready, raw, gap = _week_ready(self.repository, 2026, 1)
        self.assertTrue(ready)
        self.assertEqual((raw, gap), (1, 0))

    def test_a_completed_game_with_no_plays_holds_the_week_open(self):
        self.repository.replace_games(
            2026, [_game(99, completed=True), _game(100, completed=True)])
        replace_week_plays(self.repository, [_play("p1", 99)], season=2026, week=1)
        ready, raw, gap = _week_ready(self.repository, 2026, 1)
        self.assertFalse(ready, "game 100 finished with no plays; the week is still filling in")
        self.assertEqual(gap, 1)

    def test_an_unplayed_game_does_not_hold_the_week_open(self):
        self.repository.replace_games(
            2026, [_game(99, completed=True), _game(100, completed=False)])
        replace_week_plays(self.repository, [_play("p1", 99)], season=2026, week=1)
        self.assertTrue(_week_ready(self.repository, 2026, 1)[0])


if __name__ == "__main__":
    unittest.main()
