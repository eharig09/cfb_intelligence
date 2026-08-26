"""Box scores for the season being played.

They were only ever synced by the history phase, for seasons already over:
2025 has 468,966 player rows and 2026 had none, with nothing in the refresh
phase to change that. A game played this season had an empty box score page
and never joined the opponent-history record.

A full season is most of half a million rows, so a refresh syncs only the
weeks still moving -- and those are read with a short cache lifetime, because
the one-year TTL that is right for a finished week would freeze a Saturday
afternoon at whatever the first request happened to catch.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

from sports_aggregator.bootstrap import steps
from sports_aggregator.cfb.cfbd import FINISHED_WEEK_TTL, LIVE_WEEK_TTL
from sports_aggregator.cfb.cli import main as cli_main
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas
from sports_aggregator.scheduled_refresh import SCORES_REFRESH_STEPS


THIS_YEAR = datetime.now().year


def _game(game_id, week, *, completed):
    return Game.from_cfbd({
        "id": game_id, "season": THIS_YEAR, "week": week, "seasonType": "regular",
        "startDate": "%d-09-05T16:00:00.000Z" % THIS_YEAR, "startTimeTBD": False,
        "completed": completed, "neutralSite": False, "conferenceGame": True,
        "venue": "Stadium", "venueId": 1,
        "homeId": 1, "homeTeam": "Michigan", "homeConference": "Big Ten",
        "homePoints": 31 if completed else None,
        "awayId": 2, "awayTeam": "Ohio State", "awayConference": "Big Ten",
        "awayPoints": 17 if completed else None,
    })


class RecordingClient:
    """Notes every box-score request and what lifetime it was asked for."""

    configured = True

    def __init__(self):
        self.calls = []

    def _record(self, kind, year, week, force, cache_ttl_seconds):
        self.calls.append({"kind": kind, "year": year, "week": week,
                           "force": force, "ttl": cache_ttl_seconds})
        return []

    def game_team_box_scores(self, year, week, force=False,
                             cache_ttl_seconds=FINISHED_WEEK_TTL):
        return self._record("team", year, week, force, cache_ttl_seconds)

    def game_player_box_scores(self, year, week, force=False,
                               cache_ttl_seconds=FINISHED_WEEK_TTL):
        return self._record("player", year, week, force, cache_ttl_seconds)


class BoxScoreSyncTests(unittest.TestCase):

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
        self.repository.replace_games(THIS_YEAR, [
            _game(index, index, completed=index <= 4) for index in range(1, 7)])
        self.client = RecordingClient()

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _run(self, *extra):
        return cli_main([
            "sync-box-scores", "--year", str(THIS_YEAR),
            "--database", self.path, *extra,
        ], client=self.client)

    def test_only_the_weeks_asked_for_are_fetched(self):
        self._run("--recent-weeks", "2")
        weeks = sorted({call["week"] for call in self.client.calls})
        self.assertEqual(weeks, [3, 4], "weeks 1 and 2 are settled")

    def test_without_the_flag_every_completed_week_is_fetched(self):
        self._run()
        self.assertEqual(sorted({call["week"] for call in self.client.calls}),
                         [1, 2, 3, 4])

    def test_a_week_that_is_not_over_is_read_with_a_short_lifetime(self):
        """A year-long cache would freeze a Saturday at its first request."""
        self._run("--recent-weeks", "2")
        self.assertTrue(self.client.calls)
        for call in self.client.calls:
            with self.subTest(week=call["week"]):
                self.assertEqual(call["ttl"], LIVE_WEEK_TTL)

    def test_both_datasets_are_asked_for(self):
        self._run("--recent-weeks", "1")
        self.assertEqual({call["kind"] for call in self.client.calls},
                         {"team", "player"})

    def test_a_season_with_nothing_played_costs_nothing(self):
        self.repository.replace_games(THIS_YEAR, [
            _game(index, index, completed=False) for index in range(1, 7)])
        self._run("--recent-weeks", "2")
        self.assertEqual(self.client.calls, [])


class RefreshWiringTests(unittest.TestCase):
    """The step has to actually be in the plan, scoped, to be worth anything."""

    def _step(self):
        return next(step for step in steps(2026) if step.name == "cfbd-box-scores")

    def test_the_refresh_phase_carries_it(self):
        self.assertIn("refresh", self._step().phases)

    def test_it_is_scoped_to_recent_weeks(self):
        command = self._step().command
        self.assertIn("--recent-weeks", command)
        self.assertIn("sync-box-scores", command)

    def test_a_game_day_pass_runs_it(self):
        """Otherwise a finished game's box score waits for the next heavy run."""
        self.assertIn("cfbd-box-scores", SCORES_REFRESH_STEPS)


if __name__ == "__main__":
    unittest.main()
