"""Opponent quality belongs to the schedule, and follows the season it shows.

It measures the strength of the schedule actually played, so it sits under the
schedule table and reads the schedule's season selector. Reading the statistics
selector instead let the two disagree on screen: a 2025 schedule beside 2026
opponent ratings, with nothing to say which season the numbers described.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from app import create_app
from sports_aggregator.cfb.models import Game, Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.views import team_opponent_quality_table
from sports_aggregator.social.content import ContentRepository


class TableTests(unittest.TestCase):

    def test_a_schedule_with_no_completed_games_says_so(self):
        """Zero counts read as 'faced nobody ranked', not 'nobody has played'."""
        table = team_opponent_quality_table(
            "Boise State", {"games": 0, "elo_top_25": 0, "poll_ranked": 0},
            2026, upcoming=True)
        self.assertEqual(table.rows, [])
        # An empty table renders without its caption, so the message has to
        # name its own subject.
        self.assertIn("Opponent quality", table.empty)
        self.assertIn("2026", table.empty)

    def test_a_completed_season_with_no_stored_ratings_reads_differently(self):
        table = team_opponent_quality_table("Boise State", {"games": 0}, 2024)
        self.assertEqual(table.rows, [])
        self.assertIn("No completed 2024", table.empty)

    def test_stored_ratings_are_listed_and_never_averaged_together(self):
        table = team_opponent_quality_table(
            "Boise State",
            {"games": 12, "average_pregame_elo": 1588.4, "average_core": 0.61,
             "poll_ranked": 3},
            2025)
        metrics = [row["metric"] for row in table.rows]
        self.assertIn("Completed opponents", metrics)
        self.assertIn("Avg opponent pregame Elo", metrics)
        self.assertIn("Avg opponent CORE", metrics)
        self.assertIn("2025", table.caption)

    def test_absent_measures_are_omitted_rather_than_shown_as_blank(self):
        table = team_opponent_quality_table(
            "Boise State", {"games": 5, "average_pregame_elo": None}, 2025)
        self.assertNotIn("Avg opponent pregame Elo",
                         [row["metric"] for row in table.rows])


class PlacementTests(unittest.TestCase):
    """The rendered page: position, and which selector drives it."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 68, "school": "Boise State", "mascot": "Broncos", "abbreviation": "BSU",
             "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
             "color": "#0033A0", "logos": []},
            {"id": 21, "school": "San Diego State", "mascot": "Aztecs", "abbreviation": "SDSU",
             "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
             "color": "#A6192E", "logos": []},
        )])
        # A completed 2025 meeting and an unplayed 2026 one, so the two
        # schedule seasons genuinely differ.
        self.repository.replace_games(2025, [Game.from_cfbd({
            "id": 390, "season": 2025, "week": 5, "seasonType": "regular",
            "startDate": "2025-10-04T19:30:00.000Z", "startTimeTBD": False,
            "completed": True, "neutralSite": False, "conferenceGame": True,
            "venue": "Albertsons Stadium", "venueId": 2,
            "homeId": 68, "homeTeam": "Boise State", "homeConference": "Mountain West",
            "homePoints": 31, "awayId": 21, "awayTeam": "San Diego State",
            "awayConference": "Mountain West", "awayPoints": 17,
        })])
        self.repository.replace_games(2026, [Game.from_cfbd({
            "id": 401, "season": 2026, "week": 3, "seasonType": "regular",
            "startDate": "2026-09-12T19:30:00.000Z", "startTimeTBD": False,
            "completed": False, "neutralSite": False, "conferenceGame": True,
            "venue": "Snapdragon Stadium", "venueId": 1,
            "homeId": 21, "homeTeam": "San Diego State", "homeConference": "Mountain West",
            "homePoints": None, "awayId": 68, "awayTeam": "Boise State",
            "awayConference": "Mountain West", "awayPoints": None,
        })])
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Test", "Player", "Boise State", "QB", 1, 74, 200, 3),
        ))
        ContentRepository(self.path).initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.path)

    def _body(self, query: str = "") -> str:
        return self.client.get(
            f"/college-football/teams/68/{query}").get_data(as_text=True)

    def test_it_renders_with_the_schedule_not_the_statistics(self):
        body = self._body()
        schedule = body.index('data-mobile-tab-panel="overview"')
        statistics = body.index('data-mobile-tab-panel="stats"')
        quality = body.index("Opponent quality appears here once")
        self.assertLess(schedule, quality)
        self.assertLess(quality, statistics)

    def test_it_follows_the_schedule_season(self):
        prior = self._body("?schedule_year=2025")
        self.assertIn("2025 opponent quality", prior)
        self.assertNotIn("2026 opponent quality", prior)

    def test_the_statistics_selector_does_not_move_it(self):
        """The two selectors are independent; only the schedule one applies."""
        body = self._body("?schedule_year=2025&stats_year=2026")
        self.assertIn("2025 opponent quality", body)
        self.assertNotIn("2026 opponent quality", body)

    def test_an_unplayed_schedule_shows_no_zero_filled_measures(self):
        body = self._body()
        self.assertIn("Opponent quality appears here once 2026 games are played.", body)
        self.assertNotIn("2026 opponent quality", body)


if __name__ == "__main__":
    unittest.main()
