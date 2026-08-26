"""Choosing a refresh from the schedule, which is what makes scores timely.

The full refresh takes about eight minutes and ran four times a day, so a game
ending at 3:30pm was not on the site until 6pm and a night game's final was not
there until the next morning. A game-day pass syncs the games and the market
and stops; the question these tests hold is when to run one.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.refresh_window import (
    GAME_WINDOW_HOURS, games_in_progress, profile_for,
)
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.scheduled_refresh import (
    REFRESH_PROFILES, SCORES_DATASETS, SCORES_REFRESH_STEPS,
)


EASTERN = ZoneInfo("America/New_York")


def _game(game_id, start, *, completed=False, points=None):
    return Game.from_cfbd({
        "id": game_id, "season": 2026, "week": 1, "seasonType": "regular",
        "startDate": start, "startTimeTBD": False, "completed": completed,
        "neutralSite": False, "conferenceGame": True, "venue": "Stadium",
        "venueId": 1, "homeId": 1, "homeTeam": "Michigan",
        "homeConference": "Big Ten", "homePoints": points,
        "awayId": 2, "awayTeam": "Ohio State", "awayConference": "Big Ten",
        "awayPoints": points,
    })


class RefreshWindowTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams((
            Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None, "fbs",
                 "00274C", "FFCB05", (), ("Michigan",), None, None),
            Team(2, "Ohio State", "Buckeyes", "OSU", "Big Ten", None, "fbs",
                 "BB0000", "666666", (), ("Ohio State",), None, None),
        ))
        # Noon Eastern on a Saturday.
        self.kickoff = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _schedule(self, *games):
        self.repository.replace_games(2026, list(games))

    def _at(self, **offset):
        return self.kickoff + timedelta(**offset)

    # -- the window --------------------------------------------------------

    def test_a_game_underway_is_in_progress(self):
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        self.assertEqual(
            games_in_progress(self.repository, now=self._at(hours=2)), 1)

    def test_a_game_about_to_start_is_already_worth_a_pass(self):
        """Lines move most in the hour before kickoff."""
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        self.assertEqual(
            games_in_progress(self.repository, now=self._at(minutes=-30)), 1)

    def test_a_game_still_hours_away_is_not(self):
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        self.assertEqual(
            games_in_progress(self.repository, now=self._at(hours=-4)), 0)

    def test_a_game_whose_result_has_arrived_leaves_the_window(self):
        """This is what makes the window close on its own."""
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        self.assertEqual(games_in_progress(self.repository, now=self._at(hours=3)), 1)
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z",
                             completed=True, points=31))
        self.assertEqual(games_in_progress(self.repository, now=self._at(hours=3)), 0)

    def test_a_result_that_never_arrives_cannot_hold_the_window_open(self):
        """Otherwise one stuck game keeps the refresh in game-day mode forever."""
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        self.assertEqual(
            games_in_progress(self.repository,
                              now=self._at(hours=GAME_WINDOW_HOURS + 1)), 0)

    def test_a_slate_counts_every_game_in_play(self):
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"),
                       _game(2, "2026-09-05T17:30:00.000Z"),
                       _game(3, "2026-09-06T00:00:00.000Z"))
        self.assertEqual(games_in_progress(self.repository, now=self._at(hours=2)), 2)

    def test_an_empty_schedule_asks_for_nothing(self):
        self.assertEqual(games_in_progress(self.repository, now=self._at()), 0)

    # -- the decision ------------------------------------------------------

    @patch.dict(os.environ, {"CFB_REFRESH_HOURS": "6,12,18,23",
                             "CFB_REFRESH_HEAVY_HOURS": "6,23"}, clear=False)
    def test_a_game_in_play_beats_the_clock(self):
        """Including at an hour the clock schedule would have skipped."""
        self._schedule(_game(1, "2026-09-05T16:00:00.000Z"))
        decision = profile_for(self.repository, now=self._at(hours=2))
        self.assertEqual(decision["profile"], "scores")
        self.assertEqual(decision["reason"], "games_in_progress")
        self.assertEqual(decision["games"], 1)

    @patch.dict(os.environ, {"CFB_REFRESH_HOURS": "6,12,18,23",
                             "CFB_REFRESH_HEAVY_HOURS": "6,23"}, clear=False)
    def test_a_game_in_play_also_beats_a_heavy_hour(self):
        """Eight minutes of roster crawling is not what a Saturday needs."""
        self._schedule(_game(1, "2026-09-06T02:00:00.000Z"))
        # 11pm Eastern, which is a heavy hour, with a night game unfinished.
        decision = profile_for(
            self.repository, now=datetime(2026, 9, 6, 3, tzinfo=timezone.utc))
        self.assertEqual(decision["profile"], "scores")

    @patch.dict(os.environ, {"CFB_REFRESH_HOURS": "6,12,18,23",
                             "CFB_REFRESH_HEAVY_HOURS": "6,23"}, clear=False)
    def test_the_clock_schedule_is_unchanged_when_nothing_is_being_played(self):
        for hour, expected in ((6, "heavy"), (23, "heavy"),
                               (12, "light"), (18, "light"),
                               (9, None), (15, None)):
            with self.subTest(hour=hour):
                moment = datetime(2026, 7, 1, hour, tzinfo=EASTERN)
                self.assertEqual(
                    profile_for(self.repository, now=moment)["profile"], expected)

    @patch.dict(os.environ, {"CFB_REFRESH_HOURS": "6,12,18,23",
                             "CFB_REFRESH_HEAVY_HOURS": "6,23"}, clear=False)
    def test_an_idle_moment_says_so_rather_than_running_something(self):
        decision = profile_for(self.repository,
                               now=datetime(2026, 7, 1, 15, tzinfo=EASTERN))
        self.assertIsNone(decision["profile"])
        self.assertEqual(decision["reason"], "outside_refresh_hours")


class ScoresProfileTests(unittest.TestCase):
    """What a game-day pass is allowed to touch."""

    def test_the_profile_is_accepted_by_the_scheduler(self):
        self.assertIn("scores", REFRESH_PROFILES)

    def test_it_syncs_the_games_and_the_market_and_nothing_else(self):
        self.assertEqual(SCORES_REFRESH_STEPS, ["cfbd-sync", "cfbd-lines"])

    def test_it_reads_one_cfbd_dataset(self):
        """The roster crawl is the expensive part and none of it moves."""
        self.assertEqual(SCORES_DATASETS, ["games"])

    def test_an_unknown_profile_is_refused(self):
        from sports_aggregator.scheduled_refresh import run_scheduled_refresh
        with self.assertRaises(ValueError):
            run_scheduled_refresh(2026, profile="everything")


class RefreshEndpointTests(unittest.TestCase):
    """The trigger asks for "auto"; this is where that is turned into work."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams((
            Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None, "fbs",
                 "00274C", "FFCB05", (), ("Michigan",), None, None),
            Team(2, "Ohio State", "Buckeyes", "OSU", "Big Ten", None, "fbs",
                 "BB0000", "666666", (), ("Ohio State",), None, None),
        ))
        ContentRepository(self.path).initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
            "CFB_DATABASE_PATH": self.path,
            "CFB_REFRESH_TOKEN": "test-token",
        })
        self.client = self.app.test_client()
        self.started = []
        patcher = patch("app.subprocess.Popen", side_effect=self._record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _record(self, command, **_kwargs):
        self.started.append(command)
        return unittest.mock.MagicMock()

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _post(self, profile):
        return self.client.post(
            "/internal/cfb-refresh?profile=" + profile,
            headers={"Authorization": "Bearer test-token"})

    def test_auto_during_a_slate_starts_a_game_day_pass(self):
        kickoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        self.repository.replace_games(2026, [_game(1, kickoff)])
        response = self._post("auto")
        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["profile"], "scores")
        self.assertEqual(payload["reason"], "games_in_progress")
        self.assertIn("scores", self.started[0])

    @patch.dict(os.environ, {"CFB_REFRESH_HOURS": "", "CFB_REFRESH_HEAVY_HOURS": ""},
                clear=False)
    def test_auto_with_nothing_to_do_starts_nothing(self):
        """A 200 rather than a 202, so the trigger can tell them apart."""
        self.repository.replace_games(2026, [])
        response = self._post("auto")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "skipped")
        self.assertEqual(self.started, [])

    def test_an_explicit_profile_still_works(self):
        response = self._post("heavy")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["profile"], "heavy")
        self.assertIn("heavy", self.started[0])

    def test_an_unknown_profile_is_refused(self):
        self.assertEqual(self._post("everything").status_code, 400)

    def test_the_endpoint_is_closed_without_the_token(self):
        response = self.client.post("/internal/cfb-refresh?profile=auto")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
