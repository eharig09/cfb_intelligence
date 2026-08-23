import os
import sqlite3
import tempfile
import unittest

from sports_aggregator.cfb.lines import game_lines, initialize as initialize_lines, store_lines
from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.search import _score, search
from sports_aggregator.cfb.situations import (
    _timezone_for, haversine_miles, schedule_spot, travel_burden,
)
from sports_aggregator.cfb.transfers import rank_transfers
from sports_aggregator.social.team_reddit import (
    ALWAYS_ON_TIER, poll_plan, register,
)


TEAM_PAYLOADS = (
    {"id": 1, "school": "Ohio State", "mascot": "Buckeyes", "abbreviation": "OSU",
     "alternateNames": [], "conference": "Big Ten", "classification": "fbs",
     "color": "#BB0000", "logos": [], "location": {"id": 10, "name": "Ohio Stadium"}},
    {"id": 2, "school": "Sacramento State", "mascot": "Hornets", "abbreviation": "SAC",
     "alternateNames": [], "conference": "Mid-American", "classification": "fbs",
     "color": "#00563F", "logos": [], "location": {"id": 20, "name": "Hornet Stadium"}},
    {"id": 3, "school": "Oklahoma State", "mascot": "Cowboys", "abbreviation": "OKST",
     "alternateNames": [], "conference": "Big 12", "classification": "fbs",
     "color": "#FF7300", "logos": [], "location": {"id": 30, "name": "Boone Pickens"}},
)


class SearchScoringTests(unittest.TestCase):
    def test_match_strength_is_ordered(self):
        exact = _score("ohio state", "ohio state")
        prefix = _score("ohio state buckeyes", "ohio state")
        word = _score("the ohio state", "ohio state")
        self.assertGreater(exact[0], prefix[0])
        self.assertGreater(prefix[0], word[0])

    def test_abbreviated_forms_match_in_order(self):
        self.assertIsNotNone(_score("sacramento state", "sac state"))
        self.assertIsNotNone(_score("oklahoma state", "ok state"))
        # Order matters: a reversed abbreviation is not the same team.
        self.assertIsNone(_score("oklahoma state", "state ok"))

    def test_a_single_common_token_does_not_abbreviate(self):
        # "state" alone must not match every school through the prefix rule.
        result = _score("sacramento state", "state")
        self.assertEqual(result[1], "word match")

    def test_unrelated_text_does_not_match(self):
        self.assertIsNone(_score("ohio state", "clemson"))


class SearchTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        self.repository.replace_players(2026, (
            Player("p1", 2026, "CJ", "Carr", "Ohio State", "QB", 1, 74, 200, 3),
        ))

    def tearDown(self):
        os.unlink(self.path)

    def test_a_short_query_is_refused_rather_than_matching_everything(self):
        result = search(self.repository, "a", season=2026)
        self.assertTrue(result["too_short"])
        self.assertEqual(result["total"], 0)

    def test_teams_players_and_reasons_are_returned(self):
        result = search(self.repository, "buckeyes", season=2026)
        self.assertEqual(result["teams"][0]["school"], "Ohio State")
        self.assertIn("alias", result["teams"][0]["reason"])

    def test_punctuated_initials_find_the_roster_spelling(self):
        result = search(self.repository, "C.J. Carr", season=2026)
        self.assertEqual(result["players"][0]["name"], "CJ Carr")

    def test_abbreviated_school_names_resolve(self):
        result = search(self.repository, "sac state", season=2026)
        self.assertEqual(result["teams"][0]["school"], "Sacramento State")


class LinesTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        initialize_lines(self.repository)

    def tearDown(self):
        os.unlink(self.path)

    def test_each_provider_is_stored_separately(self):
        stored = store_lines(self.repository, 2026, [{
            "id": 100, "lines": [
                {"provider": "DraftKings", "spread": -7.5, "spreadOpen": -6.5,
                 "overUnder": 55.5, "overUnderOpen": 54.5},
                {"provider": "Bovada", "spread": -8.0, "spreadOpen": -6.5,
                 "overUnder": 56.0, "overUnderOpen": 54.5},
            ]}])
        self.assertEqual(stored, 2)
        result = game_lines(self.repository, 100)
        self.assertEqual(result["count"], 2)
        # Disagreement between books is reported, not averaged away.
        self.assertEqual(result["spread_range"], 0.5)
        self.assertEqual(result["total_range"], 0.5)

    def test_movement_since_opening_is_derived(self):
        store_lines(self.repository, 2026, [{
            "id": 101, "lines": [{"provider": "DraftKings", "spread": -9.5,
                                  "spreadOpen": -6.5, "overUnder": 50.0,
                                  "overUnderOpen": 52.0}]}])
        provider = game_lines(self.repository, 101)["providers"][0]
        self.assertEqual(provider["spread_move"], -3.0)
        self.assertEqual(provider["total_move"], -2.0)

    def test_a_game_without_quotes_returns_an_empty_packet(self):
        result = game_lines(self.repository, 999)
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["consensus_spread"])

    def test_repeated_storage_updates_rather_than_duplicates(self):
        payload = [{"id": 102, "lines": [{"provider": "Bovada", "spread": -3.0}]}]
        store_lines(self.repository, 2026, payload)
        store_lines(self.repository, 2026, [{"id": 102, "lines": [
            {"provider": "Bovada", "spread": -4.5}]}])
        result = game_lines(self.repository, 102)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["providers"][0]["spread"], -4.5)


class SituationTests(unittest.TestCase):
    def test_distance_between_known_points_is_sane(self):
        # Columbus to Los Angeles is roughly 2,000 miles.
        miles = haversine_miles((39.96, -83.00), (34.05, -118.24))
        self.assertGreater(miles, 1900)
        self.assertLess(miles, 2200)

    def test_longitudes_map_to_time_zones(self):
        self.assertEqual(_timezone_for(-118.0), "Pacific")
        self.assertEqual(_timezone_for(-83.0), "Eastern")
        self.assertIsNone(_timezone_for(None))

    def test_travel_is_none_without_venue_coordinates(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            repository = CFBRepository(path)
            repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
            game = {"home_team_id": 1, "away_team_id": 2,
                    "home_team": "Ohio State", "away_team": "Sacramento State"}
            self.assertIsNone(travel_burden(repository, game))
        finally:
            os.unlink(path)

    def test_travel_names_distance_and_zone_shift(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            repository = CFBRepository(path)
            repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
            repository.replace_venues([
                {"id": 10, "name": "Ohio Stadium", "latitude": 40.0, "longitude": -83.0},
                {"id": 20, "name": "Hornet Stadium", "latitude": 38.5, "longitude": -121.4},
            ])
            game = {"home_team_id": 1, "away_team_id": 2,
                    "home_team": "Ohio State", "away_team": "Sacramento State"}
            travel = travel_burden(repository, game)
            self.assertTrue(travel["notable"])
            self.assertEqual(travel["timezone_shift"], 3)
            self.assertIn("time zones east", travel["detail"])
        finally:
            os.unlink(path)


class TransferImpactTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        self.repository.replace_players(2025, (
            Player("starter", 2025, "Real", "Starter", "Sacramento State", "QB", 1, 74, 210, 3),
            Player("backup", 2025, "Deep", "Reserve", "Sacramento State", "QB", 2, 73, 200, 2),
        ))
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "starter", "player": "Real Starter",
             "team": "Sacramento State", "conference": "Big Sky", "position": "QB",
             "category": "passing", "statType": "ATT", "stat": "400"},
        ], "Big Sky")
        self.repository.replace_transfers(2026, (
            {"firstName": "Real", "lastName": "Starter", "position": "QB",
             "origin": "Sacramento State", "destination": "Ohio State",
             "transferDate": "2026-01-05T00:00:00Z", "rating": 0.92, "stars": 4,
             "eligibility": "Immediate"},
            {"firstName": "Deep", "lastName": "Reserve", "position": "QB",
             "origin": "Sacramento State", "destination": "Ohio State",
             "transferDate": "2026-01-06T00:00:00Z", "rating": 0.80, "stars": 3,
             "eligibility": "Immediate"},
        ))

    def tearDown(self):
        os.unlink(self.path)

    def test_prior_production_outranks_recruiting_opinion(self):
        ranked = rank_transfers(self.repository, season=2026)
        self.assertEqual(ranked[0]["player_name"], "Real Starter")
        self.assertGreater(ranked[0]["impact_score"], ranked[1]["impact_score"])
        self.assertTrue(any("ATT" in reason for reason in ranked[0]["reasons"]))

    def test_a_player_with_no_record_is_unproven_not_low_impact(self):
        ranked = rank_transfers(self.repository, season=2026)
        reserve = next(row for row in ranked if row["player_name"] == "Deep Reserve")
        self.assertEqual(reserve["impact_band"], "UNPROVEN")
        self.assertFalse(reserve["has_evidence"])
        self.assertIn("no prior-season production or grade is linked", reserve["reasons"])

    def test_filtering_by_destination_team(self):
        ranked = rank_transfers(self.repository, season=2026, team_id=1, direction="in")
        self.assertEqual(len(ranked), 2)
        self.assertEqual(rank_transfers(self.repository, season=2026, team_id=3), [])


class TeamSubredditTests(unittest.TestCase):
    """Polling every team subreddit is affordable and still the wrong default."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])

    def tearDown(self):
        os.unlink(self.path)

    def test_registration_strips_the_r_prefix(self):
        register(self.repository, [{"team_id": 1, "subreddit": "r/OhioStateFootball"}])
        connection = sqlite3.connect(self.path)
        stored = connection.execute(
            "SELECT subreddit FROM team_subreddits WHERE team_id=1").fetchone()[0]
        connection.close()
        self.assertEqual(stored, "OhioStateFootball")

    def test_unverified_entries_are_never_polled(self):
        register(self.repository, [{"team_id": 1, "subreddit": "OhioStateFootball"}])
        plan = poll_plan(self.repository, 2026)
        self.assertEqual(plan["registered"], 1)
        self.assertEqual(plan["verified"], 0)
        self.assertEqual(plan["active_this_cycle"], 0)

    def test_the_plan_reports_its_own_cost(self):
        plan = poll_plan(self.repository, 2026)
        self.assertIn("requests_per_cycle", plan)
        self.assertIn("estimated_minutes", plan)

    def test_always_on_entries_activate_without_a_game(self):
        from sports_aggregator.social.team_reddit import mark_verified
        register(self.repository, [{"team_id": 1, "subreddit": "OhioStateFootball",
                                    "tier": ALWAYS_ON_TIER}])
        mark_verified(self.repository, 1, platform_id="t5_x", subscribers=1000)
        plan = poll_plan(self.repository, 2026)
        self.assertEqual(plan["active_this_cycle"], 1)
        self.assertIn("always-on", plan["subreddits"][0]["why"])


class RosterProductionTests(unittest.TestCase):
    """Preseason production must say what is here and what is not."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        # 2025: a starter and a receiver at Sacramento State; 2026: the starter
        # has moved to Ohio State and a returning back is already there.
        self.repository.replace_players(2025, (
            Player("qb", 2025, "Moved", "Passer", "Sacramento State", "QB", 1, 74, 210, 3),
            Player("wr", 2025, "Gone", "Receiver", "Ohio State", "WR", 2, 73, 190, 4),
            Player("rb", 2025, "Stayed", "Runner", "Ohio State", "RB", 3, 71, 205, 2),
        ))
        self.repository.replace_players(2026, (
            Player("qb", 2026, "Moved", "Passer", "Ohio State", "QB", 1, 74, 210, 4),
            Player("rb", 2026, "Stayed", "Runner", "Ohio State", "RB", 3, 71, 205, 3),
        ))
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "qb", "player": "Moved Passer",
             "team": "Sacramento State", "conference": "Big Sky", "position": "QB",
             "category": "passing", "statType": "YDS", "stat": "3000"},
            {"season": 2025, "playerId": "wr", "player": "Gone Receiver",
             "team": "Ohio State", "conference": "Big Ten", "position": "WR",
             "category": "receiving", "statType": "YDS", "stat": "900"},
            {"season": 2025, "playerId": "rb", "player": "Stayed Runner",
             "team": "Ohio State", "conference": "Big Ten", "position": "RB",
             "category": "rushing", "statType": "YDS", "stat": "700"},
        ], "Big Ten")

    def tearDown(self):
        os.unlink(self.path)

    def _states(self):
        from sports_aggregator.cfb.roster_production import team_production
        production = team_production(self.repository, 1, 2026)
        return {entry["player"]: entry
                for group in production["groups"] for entry in group["players"]}

    def test_each_player_is_placed_in_the_right_state(self):
        states = self._states()
        self.assertEqual(states["Moved Passer"]["state"], "ARRIVED")
        self.assertEqual(states["Gone Receiver"]["state"], "DEPARTED")
        self.assertEqual(states["Stayed Runner"]["state"], "RETURNING")

    def test_arrived_production_names_where_it_was_earned(self):
        arrived = self._states()["Moved Passer"]
        self.assertEqual(arrived["earned_at"], "Sacramento State")

    def test_returning_production_carries_no_origin(self):
        self.assertIsNone(self._states()["Stayed Runner"]["earned_at"])

    def test_retained_share_counts_arrivals_as_present(self):
        from sports_aggregator.cfb.roster_production import team_production
        production = team_production(self.repository, 1, 2026)
        # 3000 arrived + 700 returning against 900 departed.
        self.assertGreater(production["retained_share"], 75)

    def test_projection_evidence_excludes_departed_players(self):
        from sports_aggregator.cfb.roster_production import projected_depth
        evidence = projected_depth(self.repository, 1, 2026)
        self.assertIn("qb", evidence)
        self.assertIn("rb", evidence)
        self.assertNotIn("wr", evidence)
        self.assertIn("Sacramento State", evidence["qb"]["summary"])


class ExpandedSituationTests(unittest.TestCase):
    def test_a_routine_friday_turnaround_is_not_a_short_week(self):
        from sports_aggregator.cfb.situations import SHORT_WEEK_DAYS
        # Saturday to Friday is six days and entirely normal.
        self.assertLess(SHORT_WEEK_DAYS, 6)

    def test_altitude_threshold_is_expressed_in_metres(self):
        from sports_aggregator.cfb.situations import ALTITUDE_METRES
        # CFBD publishes metres; a feet-based threshold would never fire.
        self.assertLess(ALTITUDE_METRES, 3000)


class TransferDirectionTests(unittest.TestCase):
    """A transfer story names two schools; only one is the subject."""

    def test_destination_verbs_are_recognised(self):
        from sports_aggregator.social.context import transfer_role
        self.assertEqual(transfer_role("transferred to Kentucky", "Kentucky"), "destination")
        self.assertEqual(transfer_role("commits to Texas", "Texas"), "destination")
        self.assertEqual(transfer_role("Texas lands a receiver", "Texas"), "destination")

    def test_origin_phrasing_is_recognised(self):
        from sports_aggregator.social.context import transfer_role
        self.assertEqual(transfer_role("the former Ole Miss quarterback", "Ole Miss"), "origin")
        self.assertEqual(transfer_role("an Ole Miss transfer", "Ole Miss"), "origin")

    def test_a_plain_mention_has_no_direction(self):
        from sports_aggregator.social.context import transfer_role
        self.assertIsNone(transfer_role("Georgia beat Alabama on Saturday", "Georgia"))


class EloMovementTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_movement_is_empty_without_rated_games(self):
        from sports_aggregator.social.team_reddit import elo_movement
        self.assertEqual(elo_movement(self.repository, 2026), {})

    def test_movement_measures_first_to_latest(self):
        from sports_aggregator.social.team_reddit import elo_movement
        connection = sqlite3.connect(self.path)
        connection.executemany(
            """INSERT INTO games(game_id,season,week,season_type,start_date,start_time_tbd,
               completed,neutral_site,conference_game,venue_id,venue,television,
               home_team_id,home_team,home_conference,home_points,home_pregame_elo,
               away_team_id,away_team,away_conference,away_points,away_pregame_elo,
               excitement_index,notes,updated_at)
               VALUES(?,2026,?,'regular',?,0,1,0,1,NULL,NULL,NULL,
                      7,'Riser','MAC',NULL,?,8,'Other','MAC',NULL,1500,NULL,NULL,'now')""",
            [(1, 1, "2026-09-01T00:00:00Z", 1400),
             (2, 5, "2026-10-01T00:00:00Z", 1520)])
        connection.commit()
        connection.close()
        movement = elo_movement(self.repository, 2026)
        self.assertEqual(movement[7]["change"], 120.0)
        self.assertEqual(movement[7]["samples"], 2)


class TransferIdentityTests(unittest.TestCase):
    """A transfer must reach his own page, and carry his prior grade."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(p) for p in TEAM_PAYLOADS])
        self.repository.replace_players(2025, (
            Player("qb", 2025, "Moved", "Passer", "Sacramento State", "QB", 1, 74, 210, 3),
        ))
        self.repository.replace_players(2026, (
            Player("qb", 2026, "Moved", "Passer", "Ohio State", "QB", 1, 74, 210, 4),
        ))
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "qb", "player": "Moved Passer",
             "team": "Sacramento State", "conference": "Big Sky", "position": "QB",
             "category": "passing", "statType": "ATT", "stat": "400"},
            {"season": 2025, "playerId": "qb", "player": "Moved Passer",
             "team": "Sacramento State", "conference": "Big Sky", "position": "QB",
             "category": "passing", "statType": "YDS", "stat": "3000"},
        ], "Big Sky")
        self.repository.replace_transfers(2026, (
            {"firstName": "Moved", "lastName": "Passer", "position": "QB",
             "origin": "Sacramento State", "destination": "Ohio State",
             "transferDate": "2026-01-05T00:00:00Z", "rating": 0.92, "stars": 4,
             "eligibility": "Immediate"},
        ))
        connection = sqlite3.connect(self.path)
        connection.execute(
            """INSERT INTO pff_players(season,pff_player_id,player_name,normalized_name,
               position,pff_team_name,cfbd_team_id,cfbd_team,cfbd_player_id,
               match_status,match_confidence,interest_score,updated_at)
               VALUES(2025,'p9','Moved Passer','moved passer','QB','SAC ST',2,
               'Sacramento State',NULL,'possible_transfer',0.5,88.0,'now')""")
        connection.commit()
        connection.close()

    def tearDown(self):
        os.unlink(self.path)

    def test_portal_evidence_confirms_the_pff_identity(self):
        report = self.repository.confirm_transfer_pff_links(2026)
        self.assertEqual(report["confirmed"], 1)
        connection = sqlite3.connect(self.path)
        row = connection.execute(
            "SELECT cfbd_player_id,cfbd_team,match_status FROM pff_players").fetchone()
        connection.close()
        self.assertEqual(row[0], "qb")
        # The team fields still name where the performance happened.
        self.assertEqual(row[1], "Sacramento State")
        self.assertEqual(row[2], "portal_confirmed")

    def test_confirmation_is_idempotent(self):
        self.repository.confirm_transfer_pff_links(2026)
        self.assertEqual(self.repository.confirm_transfer_pff_links(2026)["confirmed"], 0)

    def test_transfers_resolve_to_a_roster_id(self):
        ranked = rank_transfers(self.repository, season=2026, team_id=1)
        self.assertEqual(ranked[0]["player_id"], "qb")

    def test_arrival_production_appears_in_team_leaders(self):
        leaders = self.repository.team_player_leaders("Ohio State", 2026)
        passing = leaders["groups"]["passing"]["players"]
        entry = next(row for row in passing if row["player"] == "Moved Passer")
        self.assertTrue(entry["arrival"])
        self.assertEqual(entry["origin"], "Sacramento State")

    def test_a_transfer_grade_reaches_the_depth_chart(self):
        self.repository.confirm_transfer_pff_links(2026)
        depth = self.repository.team_depth_chart(1, 2026)
        players = [row for groups in depth["units"].values()
                   for rows in groups.values() for row in rows]
        entry = next(row for row in players if row["name"] == "Moved Passer")
        self.assertEqual(entry["pff_interest"], 88.0)
        self.assertEqual(entry["pff_graded_at"], "Sacramento State")

    def test_an_ambiguous_name_is_left_unlinked(self):
        # A second transfer with the same name makes the identity unsafe.
        self.repository.replace_transfers(2026, (
            {"firstName": "Moved", "lastName": "Passer", "position": "QB",
             "origin": "Sacramento State", "destination": "Ohio State",
             "transferDate": "2026-01-05T00:00:00Z", "rating": 0.92, "stars": 4,
             "eligibility": "Immediate"},
            {"firstName": "Moved", "lastName": "Passer", "position": "WR",
             "origin": "Oklahoma State", "destination": "Ohio State",
             "transferDate": "2026-01-06T00:00:00Z", "rating": 0.80, "stars": 3,
             "eligibility": "Immediate"},
        ))
        report = self.repository.confirm_transfer_pff_links(2026)
        self.assertEqual(report["confirmed"], 0)
        self.assertGreater(report["ambiguous"], 0)
