import os
import tempfile
import unittest

from app import create_app
from sports_aggregator.cfb import views
from sports_aggregator.cfb.draft_matchups import (
    OPPOSING_GROUP, annotate_board, upcoming_by_team,
)
from sports_aggregator.cfb.models import Game, PollRanking, Team
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


def team(team_id, school):
    return Team.from_cfbd({
        "id": team_id, "school": school, "mascot": school, "abbreviation": school[:3].upper(),
        "alternateNames": [], "conference": "Big Ten", "classification": "fbs",
        "color": "#0033A0", "logos": []})


def game(game_id, week, home_id, home, away_id, away, *, completed=False, start="2026-09-05"):
    return Game.from_cfbd({
        "id": game_id, "season": 2026, "week": week, "seasonType": "regular",
        "startDate": f"{start}T19:30:00.000Z", "startTimeTBD": False,
        "completed": completed, "neutralSite": False, "conferenceGame": True,
        "venue": "Stadium", "venueId": 1,
        "homeId": home_id, "homeTeam": home, "homeConference": "Big Ten", "homePoints": 20 if completed else None,
        "awayId": away_id, "awayTeam": away, "awayConference": "Big Ten", "awayPoints": 17 if completed else None})


class UpcomingTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([team(1, "Michigan"), team(2, "Ohio State"),
                                       team(3, "Purdue")])

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_the_next_unplayed_game_is_the_one_reported(self):
        self.repository.replace_games(2026, [
            game(10, 1, 2, "Ohio State", 1, "Michigan", completed=True, start="2026-09-01"),
            game(11, 2, 1, "Michigan", 3, "Purdue", start="2026-09-08"),
            game(12, 3, 3, "Purdue", 1, "Michigan", start="2026-09-15")])
        upcoming = upcoming_by_team(self.repository, 2026)
        self.assertEqual(upcoming[1]["game_id"], 11)
        self.assertEqual(upcoming[1]["opponent"], "Purdue")
        self.assertTrue(upcoming[1]["at_home"])

    def test_both_sides_of_a_game_get_an_entry(self):
        self.repository.replace_games(2026, [game(11, 2, 1, "Michigan", 3, "Purdue")])
        upcoming = upcoming_by_team(self.repository, 2026)
        self.assertEqual(upcoming[1]["opponent"], "Purdue")
        self.assertEqual(upcoming[3]["opponent"], "Michigan")
        self.assertFalse(upcoming[3]["at_home"])

    def test_a_team_with_nothing_left_to_play_is_absent(self):
        self.repository.replace_games(2026, [
            game(10, 1, 2, "Ohio State", 1, "Michigan", completed=True)])
        self.assertEqual(upcoming_by_team(self.repository, 2026), {})


class AnnotateTests(UpcomingTests):
    def setUp(self):
        super().setUp()
        self.repository.replace_games(2026, [game(11, 2, 1, "Michigan", 3, "Purdue")])

    def test_a_prospect_gets_his_next_game_and_a_watch_score(self):
        board = [{"player_name": "A Receiver", "team_id": 1,
                  "draft_position": "Wide Receiver", "interest_score": 80.0}]
        annotate_board(self.repository, board, season=2026)
        self.assertEqual(board[0]["next_opponent"], "Purdue")
        self.assertTrue(board[0]["next_is_home"])
        self.assertIsNotNone(board[0]["watch_score"])

    def test_the_opposing_group_follows_the_position(self):
        cases = {"Wide Receiver": "Secondary", "Offensive Tackle": "Edge rushers",
                 "Cornerback": "Receivers", "Defensive Edge": "Offensive tackles"}
        board = [{"team_id": 1, "draft_position": position, "interest_score": 50.0}
                 for position in cases]
        annotate_board(self.repository, board, season=2026)
        self.assertEqual([row["opposing_group"] for row in board], list(cases.values()))

    def test_an_unmapped_position_still_says_something_honest(self):
        board = [{"team_id": 1, "draft_position": "Long Snapper", "interest_score": 10.0}]
        annotate_board(self.repository, board, season=2026)
        self.assertEqual(board[0]["opposing_group"], "Opposing front")

    def test_the_consensus_board_key_is_accepted_too(self):
        """The profile board says team_id; the consensus board says cfbd_team_id."""
        board = [{"cfbd_team_id": 1, "draft_position": "Safety", "interest_score": 60.0}]
        annotate_board(self.repository, board, season=2026)
        self.assertEqual(board[0]["next_opponent"], "Purdue")

    def test_a_prospect_whose_team_is_done_is_left_alone(self):
        board = [{"team_id": 2, "draft_position": "Quarterback", "interest_score": 90.0}]
        annotate_board(self.repository, board, season=2026)
        self.assertNotIn("next_opponent", board[0])
        self.assertNotIn("watch_score", board[0])

    def test_a_better_game_lifts_the_watch_score_of_an_equal_player(self):
        """The blend is mostly the player, with a thumb on the game."""
        plain = [{"team_id": 1, "draft_position": "Safety", "interest_score": 70.0}]
        annotate_board(self.repository, plain, season=2026)
        self.repository.replace_rankings(2026, [
            PollRanking(2026, "regular", 1, "AP Top 25", False, rank, team_id,
                        school, "Big Ten", 0, 100 - rank)
            for rank, team_id, school in ((1, 1, "Michigan"), (2, 3, "Purdue"))])
        ranked = [{"team_id": 1, "draft_position": "Safety", "interest_score": 70.0}]
        annotate_board(self.repository, ranked, season=2026)
        self.assertGreater(ranked[0]["watch_score"], plain[0]["watch_score"])


class DraftTableTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False})

    def table(self, entry):
        with self.app.test_request_context("/"):
            return views.draft_watch_table([entry], 2026)

    def test_the_new_columns_are_offered(self):
        table = self.table({"rank": 1, "player_name": "A Player", "position": "WR"})
        labels = [column.label for column in table.columns]
        for label in ("Next", "Facing", "Watch"):
            self.assertIn(label, labels)

    def test_a_row_carries_the_matchup_when_it_has_one(self):
        table = self.table({
            "rank": 1, "player_name": "A Player", "position": "WR",
            "next_opponent": "Purdue", "next_is_home": False, "next_game_id": 11,
            "next_week": 2, "opposing_group": "Secondary", "opposing_grade": 63.5,
            "watch_score": 71.0})
        row = table.rows[0]
        self.assertEqual(row["next_game"], "at Purdue")
        self.assertIn("/games/11/", row["next_game_url"])
        self.assertEqual(row["next_game_sub"], "Week 2")
        self.assertEqual(row["opposing_group"], "Secondary")
        self.assertIn("63.5", row["opposing_group_sub"])
        self.assertEqual(row["watch_score"], 71.0)

    def test_a_row_without_a_game_carries_none_of_it(self):
        """An offseason board should not print a dash pretending to be a matchup."""
        row = self.table({"rank": 1, "player_name": "A Player", "position": "WR"}).rows[0]
        for key in ("next_game", "opposing_group", "watch_score"):
            self.assertNotIn(key, row)


if __name__ == "__main__":
    unittest.main()
