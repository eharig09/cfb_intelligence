from datetime import datetime, timezone
import os
import sqlite3
import tempfile
import threading
import time
import unittest

from app import create_app
from sports_aggregator.catalog import get_league
from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError
from sports_aggregator.cfb.models import Game, Player
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.sync import CFBDataSync
from sports_aggregator.cfb.web import _nearest_week_games
from sports_aggregator.models import Article
from sports_aggregator.service import AggregationResult


class WeekSelectionTests(unittest.TestCase):
    def test_week_zero_is_the_first_upcoming_week(self):
        week, games = _nearest_week_games([
            {"game_id": 2, "week": 1}, {"game_id": 1, "week": 0},
        ])
        self.assertEqual(week, 0)
        self.assertEqual([game["game_id"] for game in games], [1])


TEAM_PAYLOAD = [
    {
        "id": 1, "school": "Michigan", "mascot": "Wolverines", "abbreviation": "MICH",
        "alternateNames": ["U-M"], "conference": "Big Ten", "division": None,
        "classification": "fbs", "color": "00274C", "alternateColor": "FFCB05",
        "logos": ["https://example.com/michigan.png"],
        "location": {"id": 10, "name": "Michigan Stadium"},
    },
    {
        "id": 2, "school": "Wisconsin", "mascot": "Badgers", "abbreviation": "WIS",
        "alternateNames": [], "conference": "Big Ten", "division": None,
        "classification": "fbs", "color": "C5050C", "alternateColor": "FFFFFF",
        "logos": [], "location": {"id": 20, "name": "Camp Randall Stadium"},
    },
]

GAME_PAYLOAD = [
    {
        "id": 100, "season": 2026, "week": 2, "seasonType": "regular",
        "startDate": "2026-09-05T19:30:00Z", "startTimeTBD": False,
        "completed": False, "neutralSite": False, "conferenceGame": True,
        "venueId": 10, "venue": "Michigan Stadium", "homeId": 1,
        "homeTeam": "Michigan", "homeConference": "Big Ten", "homePoints": None,
        "homePregameElo": 1750, "awayId": 2, "awayTeam": "Wisconsin",
        "awayConference": "Big Ten", "awayPoints": None, "awayPregameElo": 1680,
        "excitementIndex": None, "notes": None,
    }
]

RANKING_PAYLOAD = [
    {
        "season": 2026, "seasonType": "regular", "week": 2,
        "polls": [{
            "poll": "AP Top 25", "isFinal": False,
            "ranks": [
                {"rank": 5, "teamId": 1, "school": "Michigan", "conference": "Big Ten", "firstPlaceVotes": 1, "points": 1200},
                {"rank": 18, "teamId": 2, "school": "Wisconsin", "conference": "Big Ten", "firstPlaceVotes": 0, "points": 500},
                {"rank": 18, "teamId": 3, "school": "Iowa", "conference": "Big Ten", "firstPlaceVotes": 0, "points": 500},
            ],
        }],
    }
]

PLAYER_PAYLOAD = [
    {"id": "p1", "firstName": "Alex", "lastName": "Example", "team": "Michigan",
     "position": "QB", "jersey": 7, "height": 74, "weight": 215, "year": 3},
]


class FakeCFBDClient:
    def teams(self, _year, _force=False): return TEAM_PAYLOAD
    def roster(self, _year, _force=False): return PLAYER_PAYLOAD
    def games(self, _year, _force=False): return GAME_PAYLOAD
    def betting_lines(self, _year, _force=False): return []
    def game_media(self, _year, _force=False): return [{"id": 100, "outlet": "ABC"}]
    def records(self, _year, _force=False):
        return [
            {"teamId": 1, "team": "Michigan", "classification": "fbs", "conference": "Big Ten", "division": None, "expectedWins": 10.2, "total": {"games": 1, "wins": 1, "losses": 0, "ties": 0}, "conferenceGames": {"games": 1, "wins": 1, "losses": 0, "ties": 0}},
            {"teamId": 2, "team": "Wisconsin", "classification": "fbs", "conference": "Big Ten", "division": None, "expectedWins": 8.1, "total": {"games": 1, "wins": 1, "losses": 0, "ties": 0}, "conferenceGames": {"games": 1, "wins": 1, "losses": 0, "ties": 0}},
        ]
    def coaches(self, year, _force=False):
        return [{"id": 11, "firstName": "Test", "lastName": "Coach",
                 "seasons": [{"teamId": 1, "school": "Michigan",
                              "conference": "Big Ten", "year": year,
                              "games": 1, "wins": 1, "losses": 0, "ties": 0,
                              "winPercentage": 1.0}]}]
    def rankings(self, _year, _force=False): return RANKING_PAYLOAD
    def team_stats(self, _year, _force=False):
        return [{"season": 2026, "team": "Michigan", "conference": "Big Ten", "statName": "yardsPerRush", "statValue": 5.4}]
    def advanced_team_stats(self, _year, _force=False):
        return [
            {"season": 2026, "team": "Michigan", "conference": "Big Ten", "offense": {"successRate": .51, "explosiveness": 1.2, "ppa": .25, "pointsPerOpportunity": 4.8, "havoc": {"total": .12}}, "defense": {"successRate": .32, "explosiveness": .8, "ppa": -.1, "pointsPerOpportunity": 2.1, "havoc": {"total": .21}}},
            {"season": 2026, "team": "Wisconsin", "conference": "Big Ten", "offense": {"successRate": .45, "explosiveness": 1.0, "ppa": .18, "pointsPerOpportunity": 4.1, "havoc": {"total": .15}}, "defense": {"successRate": .37, "explosiveness": .9, "ppa": -.02, "pointsPerOpportunity": 2.8, "havoc": {"total": .18}}},
        ]
    def core_ratings(self, _year, _force=False):
        return [{"year": 2026, "throughSeasonType": "regular", "throughWeek": 1, "team": "Michigan", "conference": "Big Ten", "overall": 18.2, "offense": 10.1, "defense": -8.1, "offensePlays": 70, "defensePlays": 65, "modelVersion": "test"}]


class StubReporting:
    def aggregate(self, league):
        return AggregationResult(
            league=league,
            articles=(Article(title="Game story", url="https://example.com/game", source="Example", reliability=4),),
            errors=(), fetched_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )


class CFBRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = CFBRepository(os.path.join(self.temp_dir.name, "cfb.sqlite3"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_transactions_wait_for_the_existing_writer(self):
        self.repository.initialize()
        blocker = sqlite3.connect(self.repository.path)
        blocker.execute("BEGIN IMMEDIATE")
        outcome = []

        def write_after_blocker():
            try:
                with self.repository.transaction() as connection:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS cfb_lock_probe(value INTEGER)")
                    connection.execute("INSERT INTO cfb_lock_probe VALUES(1)")
                outcome.append("committed")
            except Exception as exc:  # pragma: no cover - asserted below
                outcome.append(f"{type(exc).__name__}: {exc}")

        writer = threading.Thread(target=write_after_blocker)
        writer.start()
        time.sleep(0.1)
        self.assertTrue(writer.is_alive())
        blocker.commit()
        blocker.close()
        writer.join(timeout=3)
        self.assertEqual(outcome, ["committed"])
        connection = self.repository._connect()
        try:
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 60_000)
        finally:
            connection.close()

    def test_opponent_quality_uses_kickoff_elo_and_keeps_models_separate(self):
        completed = {**GAME_PAYLOAD[0], "completed": True, "homePoints": 31,
                     "awayPoints": 17}
        self.repository.replace_games(2026, [Game.from_cfbd(completed)])
        with self.repository.transaction() as connection:
            connection.execute(
                "INSERT INTO core_ratings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (2026, "regular", 2, "Wisconsin", "Big Ten", 8.5, 4.0,
                 -4.5, 120, 118, "test"))
            connection.execute(
                "INSERT INTO rankings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (2026, "regular", 2, "AP Top 25", 0, 18, 2, "Wisconsin",
                 "Big Ten", 0, 500,))
        quality = self.repository.opponent_quality(1, 2026)
        self.assertEqual(quality["games"], 1)
        self.assertEqual(quality["average_pregame_elo"], 1680)
        self.assertEqual(quality["average_core"], 8.5)
        self.assertEqual(quality["poll_ranked"], 1)

    def test_syncs_canonical_entities_aliases_and_preview_data(self):
        report = CFBDataSync(FakeCFBDClient(), self.repository).sync(2026)
        self.assertTrue(report.succeeded)
        self.assertEqual(self.repository.status(2026)["counts"]["games"], 1)
        self.assertEqual(self.repository.status(2026)["counts"]["players"], 1)
        self.assertEqual(self.repository.resolve_team_alias("U-M")[0]["team_id"], 1)
        rankings = self.repository.latest_rankings(2026)
        self.assertEqual(rankings["poll"], "AP Top 25")
        game = self.repository.get_game(100)
        self.assertEqual(game["television"], "ABC")
        self.repository.replace_games(2026, (Game.from_cfbd(item) for item in GAME_PAYLOAD))
        self.assertEqual(self.repository.get_game(100)["television"], "ABC")
        self.assertEqual(game["records"]["Michigan"]["wins"], 1)
        self.assertAlmostEqual(game["advanced_metrics"]["Wisconsin"]["offense_success_rate"], .45)
        standings = self.repository.conference_standings("Big Ten", 2026)
        self.assertEqual(standings[0]["conference_wins"], 1)

    def test_player_stat_leaders_and_preview_routes(self):
        CFBDataSync(FakeCFBDClient(), self.repository).sync(2026)
        historical_game = {**GAME_PAYLOAD[0], "id": 99, "season": 2025,
                           "startDate": "2025-09-06T19:30:00Z", "completed": True,
                           "homePoints": 21, "awayPoints": 14}
        self.repository.replace_games(2025, (Game.from_cfbd(historical_game),))
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "p1", "player": "Alex Example",
             "team": "Michigan", "conference": "Big Ten", "position": "QB",
             "category": "passing", "statType": "YDS", "stat": "3120"},
            {"season": 2025, "playerId": "p1", "player": "Alex Example",
             "team": "Michigan", "conference": "Big Ten", "position": "QB",
             "category": "passing", "statType": "ATT", "stat": "300"},
            {"season": 2025, "playerId": "p1", "player": "Alex Example",
             "team": "Michigan", "conference": "Big Ten", "position": "QB",
             "category": "passing", "statType": "TD", "stat": "27"},
            {"season": 2025, "playerId": "trick", "player": "Trick Play",
             "team": "Michigan", "conference": "Big Ten", "position": "WR",
             "category": "passing", "statType": "YDS", "stat": "40"},
            {"season": 2025, "playerId": "trick", "player": "Trick Play",
             "team": "Michigan", "conference": "Big Ten", "position": "WR",
             "category": "passing", "statType": "ATT", "stat": "1"},
            {"season": 2025, "playerId": "p2", "player": "Runner Example",
             "team": "Wisconsin", "conference": "Big Ten", "position": "RB",
             "category": "rushing", "statType": "YDS", "stat": "1400"},
            {"season": 2025, "playerId": "p2", "player": "Runner Example",
             "team": "Wisconsin", "conference": "Big Ten", "position": "RB",
             "category": "rushing", "statType": "CAR", "stat": "240"},
        ], "Big Ten")
        leaders = self.repository.conference_player_leaders("Big Ten", 2026)
        self.assertEqual(leaders["season"], 2025)
        passing = leaders["groups"]["passing"]
        self.assertEqual(passing["players"][0]["player"], "Alex Example")
        # The whole category stat line travels with each leader, not just the
        # statistic the leaderboard is ranked by.
        self.assertEqual(passing["players"][0]["stats"]["TD"], 27)
        # A single trick-play attempt does not qualify as a passing leader.
        self.assertNotIn("Trick Play", [row["player"] for row in passing["players"]])
        self.assertEqual(passing["qualifier"], "min 25 ATT")

        app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
            "LEAGUE_AGGREGATION_SERVICE": StubReporting(),
        })
        client = app.test_client()
        self.assertEqual(client.get("/college-football/conferences/big-ten/").status_code, 200)
        self.assertIn(b"Conference player leaders", client.get("/college-football/conferences/big-ten/").data)
        self.assertEqual(client.get("/college-football/teams/1/").status_code, 200)
        current_team = client.get("/college-football/teams/1/?season=2025")
        self.assertIn(b"Upcoming schedule", current_team.data)
        self.assertIn(b"Upcoming (2026)", current_team.data)
        self.assertNotIn(b"W 21-14", current_team.data)
        prior_schedule = client.get("/college-football/teams/1/?schedule_year=2025")
        self.assertIn(b"2025 schedule", prior_schedule.data)
        self.assertIn(b"W 21-14", prior_schedule.data)
        self.assertEqual(client.get("/college-football/teams/1/history/").status_code, 200)
        self.assertEqual(client.get("/college-football/teams/1/history/stats/").status_code, 200)
        self.assertEqual(client.get("/college-football/games/100/").status_code, 200)
        self.assertIn(b"Matchups to watch", client.get("/college-football/games/100/").data)
        self.assertEqual(client.get("/api/v1/cfb/conferences/big-ten").status_code, 200)
        self.assertEqual(client.get("/api/v1/cfb/teams/1").get_json()["team"]["school"], "Michigan")
        self.assertEqual(client.get("/api/v1/cfb/games/100/preview").status_code, 200)

    def test_prior_year_leader_fallback_excludes_players_not_on_current_roster(self):
        CFBDataSync(FakeCFBDClient(), self.repository).sync(2026)
        self.repository.replace_player_stats(2025, [
            {"playerId": "p1", "player": "Alex Example", "team": "Michigan",
             "conference": "Big Ten", "position": "QB", "category": "passing",
             "statType": "YDS", "stat": 3000},
            {"playerId": "p1", "player": "Alex Example", "team": "Michigan",
             "conference": "Big Ten", "position": "QB", "category": "passing",
             "statType": "ATT", "stat": 350},
            {"playerId": "gone", "player": "Departed Star", "team": "Michigan",
             "conference": "Big Ten", "position": "QB", "category": "passing",
             "statType": "YDS", "stat": 4500},
            {"playerId": "gone", "player": "Departed Star", "team": "Michigan",
             "conference": "Big Ten", "position": "QB", "category": "passing",
             "statType": "ATT", "stat": 500},
        ], "Big Ten")
        leaders = self.repository.team_player_leaders("Michigan", 2026)
        names = [row["player"] for row in leaders["groups"]["passing"]["players"]]
        self.assertIn("Alex Example", names)
        self.assertNotIn("Departed Star", names)

    def test_dashboard_and_game_api_use_persisted_data(self):
        CFBDataSync(FakeCFBDClient(), self.repository).sync(2026)
        app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository,
            "CFB_DEFAULT_SEASON": 2026,
            "LEAGUE_AGGREGATION_SERVICE": StubReporting(),
        })
        client = app.test_client()
        dashboard = client.get("/college-football/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Michigan", dashboard.data)
        self.assertIn(b"Games to Watch", dashboard.data)
        historical_query = client.get("/college-football/?season=2025")
        self.assertIn(b"2026", historical_query.data)
        game = client.get("/api/v1/cfb/games/100")
        self.assertEqual(game.status_code, 200)
        self.assertEqual(game.get_json()["television"], "ABC")
        watch = client.get("/api/v1/cfb/games-to-watch")
        self.assertEqual(watch.get_json()["games"][0]["game_id"], 100)
        teams = client.get("/api/v1/cfb/teams?conference=Big%20Ten")
        self.assertEqual(teams.get_json()["count"], 2)

    def test_roster_lifecycle_depth_board_and_player_page(self):
        CFBDataSync(FakeCFBDClient(), self.repository).sync(2026)
        self.repository.replace_players(2025, (
            Player("p1", 2025, "Alex", "Example", "Michigan", "QB", 7, 74, 215, 3),
            Player("draft1", 2025, "Dan", "Drafted", "Michigan", "DE", 9, 76, 255, 4),
            Player("transfer1", 2025, "Tom", "Transfer", "Michigan", "WR", 2, 72, 190, 2),
            Player("grad1", 2025, "Gary", "Graduate", "Michigan", "OL", 70, 77, 310, 4),
        ))
        self.repository.replace_transfers(2026, ({
            "firstName": "Tom", "lastName": "Transfer", "position": "WR",
            "origin": "Michigan", "destination": "Wisconsin",
            "transferDate": "2026-01-10T00:00:00Z", "rating": 0.91,
            "stars": 4, "eligibility": "Immediate",
        },))
        self.repository.replace_draft_picks(2026, ({
            "overall": 20, "round": 1, "pick": 20, "collegeAthleteId": "draft1",
            "collegeId": 1, "collegeTeam": "Michigan", "collegeConference": "Big Ten",
            "nflTeamId": 10, "nflTeam": "Example Pros", "name": "Dan Drafted",
            "position": "DE", "preDraftRanking": 15,
            "preDraftPositionRanking": 2, "preDraftGrade": 90,
        },))
        movements = self.repository.roster_movements(1, 2026)
        by_name = {row["name"]: row for row in movements["departures"]}
        self.assertEqual(by_name["Dan Drafted"]["movement_type"], "DRAFTED")
        self.assertEqual(by_name["Tom Transfer"]["movement_type"], "TRANSFER_OUT")
        self.assertEqual(by_name["Gary Graduate"]["movement_type"], "ELIGIBILITY_DEPARTURE")
        depth = self.repository.team_depth_chart(1, 2026)
        self.assertEqual(depth["summary"]["returners"], 1)

        connection = sqlite3.connect(self.repository.path)
        connection.executemany(
            """INSERT INTO pff_players(season,pff_player_id,player_name,normalized_name,
               position,pff_team_name,cfbd_team_id,cfbd_team,cfbd_player_id,
               match_status,match_confidence,interest_score,updated_at)
               VALUES(2025,?,?,?,?,?,?,?,?,'CONFIRMED',1.0,?,'now')""", (
                ("pff-returner", "Alex Example", "alex example", "QB", "MICH",
                 1, "Michigan", "p1", 90.0),
                ("pff-drafted", "Dan Drafted", "dan drafted", "DE", "MICH",
                 1, "Michigan", "draft1", 95.0),
            ))
        connection.commit()
        connection.close()
        conference_players = self.repository.conference_pff_players(
            "Big Ten", 2025, roster_season=2026)
        self.assertEqual([row["player_name"] for row in conference_players], ["Alex Example"])
        self.assertEqual(conference_players[0]["roster_status"], "RETURNING")

        app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
            "LEAGUE_AGGREGATION_SERVICE": StubReporting(),
        })
        client = app.test_client()
        self.assertEqual(client.get("/college-football/players/p1/").status_code, 200)
        self.assertIn(b"Career path", client.get("/college-football/players/p1/").data)
        self.assertEqual(client.get("/api/v1/cfb/players/p1").get_json()["name"], "Alex Example")


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class FakeSession:
    def __init__(self, payload): self.payload = payload; self.calls = []
    def get(self, url, **kwargs): self.calls.append((url, kwargs)); return FakeResponse(self.payload)


class CFBDClientTests(unittest.TestCase):
    def test_requires_key_and_caches_raw_authenticated_response(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CFBDConfigurationError):
                CFBDClient(api_key="", raw_cache_path=directory).teams(2026)

            session = FakeSession(TEAM_PAYLOAD)
            client = CFBDClient(api_key="secret", raw_cache_path=directory, session=session)
            self.assertEqual(len(client.teams(2026)), 2)
            self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer secret")
            self.assertEqual(len(client.teams(2026)), 2)
            self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
