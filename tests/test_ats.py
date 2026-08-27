"""Records against the number, bounded by the coach who set them.

The stored spread is signed against the home team, so getting the sign wrong
would report every away side's record as its opposite and still look plausible.
These tests pin the arithmetic to games whose outcome is obvious by hand.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import closing

from sports_aggregator.cfb.ats import (
    MINIMUM_GAMES, against_the_spread, coach_tenure,
)
from sports_aggregator.cfb.lines import initialize as lines_initialize
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


def _game(game_id, season, home_points, away_points):
    return Game.from_cfbd({
        "id": game_id, "season": season, "week": 1, "seasonType": "regular",
        "startDate": "%d-09-05T16:00:00.000Z" % season, "startTimeTBD": False,
        "completed": True, "neutralSite": False, "conferenceGame": True,
        "venue": "Stadium", "venueId": 1,
        "homeId": 1, "homeTeam": "Home", "homeConference": "Big Ten",
        "homePoints": home_points,
        "awayId": 2, "awayTeam": "Away", "awayConference": "Big Ten",
        "awayPoints": away_points,
    })


class AgainstTheSpreadTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams((
            Team(1, "Home", "H", "HOM", "Big Ten", None, "fbs", "000000",
                 "ffffff", (), ("Home",), None, None),
            Team(2, "Away", "A", "AWY", "Big Ten", None, "fbs", "000000",
                 "ffffff", (), ("Away",), None, None),
        ))
        lines_initialize(self.repository)

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _seed(self, games, *, coach=("1", "Head", "Coach"), seasons=(2025,)):
        """`games` is (game_id, season, home_points, away_points, spread, total)."""
        self.repository.replace_games(
            games[0][1], [_game(g[0], g[1], g[2], g[3]) for g in games])
        with closing(self.repository._connect()) as connection:
            for season in seasons:
                for team_id, team in ((1, "Home"), (2, "Away")):
                    connection.execute(
                        """INSERT OR REPLACE INTO coach_seasons
                           (season,coach_id,first_name,last_name,team_id,team,
                            conference,games,wins,losses,ties,win_percentage,
                            attribution_complete,updated_at)
                           VALUES(?,?,?,?,?,?,'Big Ten',0,0,0,0,0,1,'')""",
                        (season, coach[0], coach[1], coach[2], team_id, team))
            for game_id, season, _h, _a, spread, total in games:
                connection.execute(
                    """INSERT INTO game_lines(game_id,season,provider,spread,
                       over_under,formatted_spread,fetched_at)
                       VALUES(?,?,'BOOK',?,?,'','')""",
                    (game_id, season, spread, total))
            connection.commit()

    # -- the sign, which is the thing worth getting wrong -------------------

    def test_a_home_favourite_winning_by_more_than_the_number_covers(self):
        """Spread -7 means home favoured by 7; home wins by 10."""
        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        home = against_the_spread(self.repository, 1, season=2025)
        away = against_the_spread(self.repository, 2, season=2025)
        self.assertEqual(home["ats_record"], "1-0")
        self.assertEqual(away["ats_record"], "0-1", "the two sides are opposites")

    def test_a_home_favourite_winning_by_less_than_the_number_does_not(self):
        self._seed([(1, 2025, 24, 21, -7.0, 45.0)])
        self.assertEqual(
            against_the_spread(self.repository, 1, season=2025)["ats_record"], "0-1")
        self.assertEqual(
            against_the_spread(self.repository, 2, season=2025)["ats_record"], "1-0")

    def test_an_away_favourite_is_read_the_same_way(self):
        """Spread +10 means the away side is favoured by 10; it wins by 14."""
        self._seed([(1, 2025, 10, 24, 10.0, 45.0)])
        self.assertEqual(
            against_the_spread(self.repository, 2, season=2025)["ats_record"], "1-0")
        self.assertEqual(
            against_the_spread(self.repository, 1, season=2025)["ats_record"], "0-1")

    def test_landing_exactly_on_the_number_is_a_push(self):
        """Neither a win nor a loss, and counted apart from both."""
        self._seed([(1, 2025, 28, 21, -7.0, 45.0)])
        home = against_the_spread(self.repository, 1, season=2025)
        self.assertEqual(home["ats_record"], "0-0-1")
        self.assertEqual(home["pushes"], 1)
        self.assertIsNone(home["cover_rate"], "a push grades nothing")

    # -- totals -------------------------------------------------------------

    def test_the_total_is_the_same_for_both_teams(self):
        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        for team_id in (1, 2):
            with self.subTest(team=team_id):
                record = against_the_spread(self.repository, team_id, season=2025)
                self.assertEqual(record["total_record"], "1-0", "52 points over 45")

    def test_a_total_landing_exactly_is_a_push(self):
        self._seed([(1, 2025, 24, 21, -7.0, 45.0)])
        self.assertEqual(
            against_the_spread(self.repository, 1, season=2025)["total_record"], "0-0-1")

    # -- the tenure boundary ------------------------------------------------

    def test_only_games_under_the_current_coach_are_counted(self):
        games = [(1, 2024, 45, 0, -7.0, 45.0), (2, 2025, 45, 0, -7.0, 45.0)]
        self.repository.replace_games(2024, [_game(1, 2024, 45, 0)])
        self.repository.replace_games(2025, [_game(2, 2025, 45, 0)])
        with closing(self.repository._connect()) as connection:
            for season, coach in ((2024, ("old", "Former", "Boss")),
                                  (2025, ("new", "Current", "Boss"))):
                connection.execute(
                    """INSERT OR REPLACE INTO coach_seasons
                       (season,coach_id,first_name,last_name,team_id,team,
                        conference,games,wins,losses,ties,win_percentage,
                        attribution_complete,updated_at)
                       VALUES(?,?,?,?,1,'Home','Big Ten',0,0,0,0,0,1,'')""",
                    (season, coach[0], coach[1], coach[2]))
            for game_id, season, *_rest in games:
                connection.execute(
                    """INSERT INTO game_lines(game_id,season,provider,spread,
                       over_under,formatted_spread,fetched_at)
                       VALUES(?,?,'BOOK',-7.0,45.0,'','')""", (game_id, season))
            connection.commit()
        record = against_the_spread(self.repository, 1, season=2025)
        self.assertEqual(record["coach"], "Current Boss")
        self.assertEqual(record["since"], 2025)
        self.assertEqual(record["games"], 1, "the 2024 win belongs to somebody else")

    def test_a_tenure_reaching_the_oldest_stored_season_says_so(self):
        """Kirby Smart took Georgia in 2016; the coach seasons start in 2019."""
        self._seed([(1, 2025, 31, 21, -7.0, 45.0)], seasons=(2025,))
        tenure = coach_tenure(self.repository, 1, season=2025)
        self.assertTrue(tenure["truncated"])
        self.assertIn("or earlier",
                      against_the_spread(self.repository, 1, season=2025)["span"])

    def test_a_team_with_no_coach_on_record_has_no_record(self):
        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        self.assertIsNone(against_the_spread(self.repository, 99, season=2025))

    # -- honesty about the sample ------------------------------------------

    def test_a_short_sample_is_marked_provisional(self):
        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        self.assertTrue(against_the_spread(self.repository, 1, season=2025)["provisional"])

    def test_a_full_sample_is_not(self):
        games = [(index, 2025, 31, 21, -7.0, 45.0) for index in range(1, MINIMUM_GAMES + 1)]
        self._seed(games)
        self.assertFalse(against_the_spread(self.repository, 1, season=2025)["provisional"])

    def test_a_game_with_no_line_is_not_counted(self):
        self.repository.replace_games(2025, [_game(1, 2025, 31, 21)])
        with closing(self.repository._connect()) as connection:
            connection.execute(
                """INSERT OR REPLACE INTO coach_seasons
                   (season,coach_id,first_name,last_name,team_id,team,conference,
                    games,wins,losses,ties,win_percentage,attribution_complete,updated_at)
                   VALUES(2025,'1','Head','Coach',1,'Home','Big Ten',0,0,0,0,0,1,'')""")
            connection.commit()
        self.assertIsNone(against_the_spread(self.repository, 1, season=2025))


if __name__ == "__main__":
    unittest.main()


class SeasonAndAppliedNumberTests(AgainstTheSpreadTests):
    """The other two readings shown beside the tenure record."""

    def test_this_season_counts_only_this_season(self):
        from sports_aggregator.cfb.ats import season_record

        self.repository.replace_games(2024, [_game(1, 2024, 45, 0)])
        self.repository.replace_games(2025, [_game(2, 2025, 45, 0)])
        with closing(self.repository._connect()) as connection:
            for game_id, season in ((1, 2024), (2, 2025)):
                connection.execute(
                    """INSERT INTO game_lines(game_id,season,provider,spread,
                       over_under,formatted_spread,fetched_at)
                       VALUES(?,?,'BOOK',-7.0,45.0,'','')""", (game_id, season))
            connection.commit()
        record = season_record(self.repository, 1, season=2025)
        self.assertEqual(record["games"], 1)
        self.assertEqual(record["label"], "2025")

    def test_a_season_with_nothing_played_has_no_record(self):
        """The row keeps its heading; the numbers are simply not there yet."""
        from sports_aggregator.cfb.ats import season_record

        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        self.assertIsNone(season_record(self.repository, 1, season=2026))

    # -- one number applied backwards --------------------------------------

    def test_the_applied_number_grades_every_game_against_the_same_total(self):
        """Not each game against its own; that is what the season row says."""
        from sports_aggregator.cfb.ats import versus_total

        self._seed([(1, 2025, 31, 21, -7.0, 70.0),   # 52 points
                    (2, 2025, 10, 7, -7.0, 20.0),    # 17 points
                    (3, 2025, 28, 24, -7.0, 40.0)])  # 52 points
        applied = versus_total(self.repository, 1, season=2025, total=47.5)
        self.assertEqual(applied["over"], 2, "52 and 52 clear 47.5")
        self.assertEqual(applied["under"], 1, "17 does not")
        self.assertEqual(applied["record"], "2-1")
        self.assertEqual(applied["rate"], 66.7)

    def test_a_game_landing_exactly_on_the_applied_number_is_a_push(self):
        from sports_aggregator.cfb.ats import versus_total

        self._seed([(1, 2025, 24, 24, -7.0, 45.0)])
        applied = versus_total(self.repository, 1, season=2025, total=48.0)
        self.assertEqual(applied["record"], "0-0-1")

    def test_it_counts_games_with_no_stored_line(self):
        """It needs the result and tonight's number, not last week's quote."""
        from sports_aggregator.cfb.ats import versus_total

        self.repository.replace_games(2025, [_game(1, 2025, 31, 21)])
        applied = versus_total(self.repository, 1, season=2025, total=40.0)
        self.assertEqual(applied["games"], 1)
        self.assertEqual(applied["over"], 1)

    def test_no_number_means_no_reading(self):
        from sports_aggregator.cfb.ats import versus_total

        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        self.assertIsNone(versus_total(self.repository, 1, season=2025, total=None))

    # -- the packet the page reads -----------------------------------------

    def test_the_matchup_packet_carries_all_three_for_both_sides(self):
        from sports_aggregator.cfb.ats import matchup_ats

        self._seed([(1, 2025, 31, 21, -7.0, 45.0)])
        packet = matchup_ats(
            self.repository,
            {"season": 2025, "home_team_id": 1, "away_team_id": 2}, total=47.5)
        self.assertEqual(sorted(packet), ["away", "home"])
        for side in ("away", "home"):
            with self.subTest(side=side):
                self.assertEqual(sorted(packet[side]), ["season", "tenure", "versus"])
                self.assertIsNotNone(packet[side]["versus"])
