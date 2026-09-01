import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from app import create_app
from sports_aggregator.cfb.game_phases import (
    MIN_PHASE_PLAYS, game_phases, phase_margin, team_season_phases,
)
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas

NOW = "2026-01-01T00:00:00+00:00"


class PhaseFixture(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        from sports_aggregator.cfb.game_phases import game_phases as _warm
        _warm(self.repository, 0)   # builds every table the module reads
        self.play = 0

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def snap(self, offense, defense, period, epa, *, yards=5, game_id=1, season=2026,
             kind="rush", garbage=0):
        self.play += 1
        pid = f"s{self.play}"
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("INSERT INTO cfb_plays(play_id,game_id,season,week,period,offense,"
                      "defense,home_team,away_team,play_type,play_text,yards_gained,"
                      "raw_json,imported_at) VALUES (?,?,?,1,?,?,?,?,?,'Rush','run',?,'{}',?)",
                      (pid, game_id, season, period, offense, defense, defense, offense,
                       yards, NOW))
            c.execute("INSERT INTO cfb_play_epa(play_id,model_version,epa,possession_changed,"
                      "scored_at) VALUES (?,'ep-v2',?,0,?)", (pid, epa, NOW))
            c.execute("INSERT INTO cfb_play_metrics(play_id,metric_version,rush_pass,success,"
                      "garbage_time,derived_at) VALUES (?,'pbp-v1',?,1,?,?)",
                      (pid, kind, garbage, NOW))
            c.commit()

    def fill(self, offense, defense, period, epa, count=MIN_PHASE_PLAYS, **kwargs):
        for _ in range(count):
            self.snap(offense, defense, period, epa, **kwargs)


class GamePhaseTests(PhaseFixture):
    def test_quarters_and_halves_are_both_reported(self):
        self.fill("Michigan", "Ohio State", 1, 0.5)
        self.fill("Michigan", "Ohio State", 3, -0.5)
        data = game_phases(self.repository, 1)["Michigan"]
        self.assertAlmostEqual(data["quarters"][1]["epa_per_play"], 0.5)
        self.assertAlmostEqual(data["quarters"][3]["epa_per_play"], -0.5)
        self.assertAlmostEqual(data["halves"]["first"]["epa_per_play"], 0.5)
        self.assertAlmostEqual(data["halves"]["second"]["epa_per_play"], -0.5)

    def test_a_half_is_the_sum_of_its_quarters_not_their_average(self):
        """Weighted by plays, so a busy quarter counts for more."""
        self.fill("Michigan", "Ohio State", 3, 1.0, count=10)
        self.fill("Michigan", "Ohio State", 4, 0.0, count=30)
        half = game_phases(self.repository, 1)["Michigan"]["halves"]["second"]
        self.assertEqual(half["plays"], 40)
        self.assertAlmostEqual(half["epa_per_play"], 10 / 40)

    def test_every_regulation_quarter_is_present_even_when_empty(self):
        self.fill("Michigan", "Ohio State", 1, 0.5)
        quarters = game_phases(self.repository, 1)["Michigan"]["quarters"]
        self.assertEqual(sorted(quarters), [1, 2, 3, 4])
        self.assertEqual(quarters[4]["plays"], 0)
        self.assertIsNone(quarters[4]["epa_per_play"])

    def test_overtime_is_left_out(self):
        """Overtime starts at the twenty-five with no clock; it is another game."""
        self.fill("Michigan", "Ohio State", 1, 0.5)
        self.fill("Michigan", "Ohio State", 5, 9.0, count=20)
        data = game_phases(self.repository, 1)["Michigan"]
        self.assertEqual(data["game"]["plays"], MIN_PHASE_PLAYS)
        self.assertAlmostEqual(data["game"]["epa_per_play"], 0.5)

    def test_garbage_time_is_excluded(self):
        self.fill("Michigan", "Ohio State", 4, 1.0)
        self.fill("Michigan", "Ohio State", 4, 9.0, count=20, garbage=1)
        self.assertEqual(game_phases(self.repository, 1)["Michigan"]["quarters"][4]["plays"],
                         MIN_PHASE_PLAYS)

    def test_totals_come_back_beside_the_rates(self):
        self.fill("Michigan", "Ohio State", 1, 0.5, yards=7)
        quarter = game_phases(self.repository, 1)["Michigan"]["quarters"][1]
        self.assertEqual(quarter["yards"], 7 * MIN_PHASE_PLAYS)
        self.assertAlmostEqual(quarter["yards_per_play"], 7)

    def test_a_game_with_nothing_scored_is_empty_not_an_error(self):
        self.assertEqual(game_phases(self.repository, 999), {})


class PhaseMarginTests(PhaseFixture):
    def test_the_better_side_of_a_phase_is_named(self):
        self.fill("Michigan", "Ohio State", 4, 1.0)
        self.fill("Ohio State", "Michigan", 4, -1.0)
        margin = phase_margin(game_phases(self.repository, 1), "quarters", 4)
        self.assertEqual(margin["winner"], "Michigan")
        self.assertAlmostEqual(margin["margin"], 2.0)

    def test_a_thin_phase_yields_no_verdict(self):
        self.snap("Michigan", "Ohio State", 4, 1.0)
        self.snap("Ohio State", "Michigan", 4, -1.0)
        self.assertIsNone(phase_margin(game_phases(self.repository, 1), "quarters", 4))

    def test_a_tie_yields_no_verdict(self):
        self.fill("Michigan", "Ohio State", 4, 0.25)
        self.fill("Ohio State", "Michigan", 4, 0.25)
        self.assertIsNone(phase_margin(game_phases(self.repository, 1), "quarters", 4))

    def test_the_whole_game_can_be_compared_too(self):
        self.fill("Michigan", "Ohio State", 2, 1.0)
        self.fill("Ohio State", "Michigan", 2, 0.0)
        self.assertEqual(phase_margin(game_phases(self.repository, 1), "game", None)["winner"],
                         "Michigan")


class SeasonPhaseTests(PhaseFixture):
    def test_offence_and_the_defence_it_played_are_separate(self):
        self.fill("Michigan", "Ohio State", 4, 1.0, game_id=1)
        self.fill("Ohio State", "Michigan", 4, -0.5, game_id=1)
        season = team_season_phases(self.repository, "Michigan", 2026)
        self.assertAlmostEqual(season["offense"]["quarters"][4]["epa_per_play"], 1.0)
        self.assertAlmostEqual(season["defense"]["quarters"][4]["epa_per_play"], -0.5)

    def test_a_team_that_has_not_played_reports_nothing(self):
        self.assertEqual(
            team_season_phases(self.repository, "Nobody", 2026)["offense"]["game"]["plays"], 0)


class PhaseRenderTests(PhaseFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DATABASE_PATH": self.path})

    def render(self, game):
        with self.app.test_request_context("/"):
            return str(self.app.jinja_env.globals["game_phase_epa"](game))

    def test_the_block_shows_every_quarter_and_both_halves(self):
        for period in (1, 2, 3, 4):
            self.fill("Michigan", "Ohio State", period, 0.5)
            self.fill("Ohio State", "Michigan", period, -0.5)
        html = self.render({"game_id": 1, "away_team": "Michigan", "home_team": "Ohio State"})
        for label in ("Q1", "Q2", "Q3", "Q4", "1st half", "2nd half", "Game"):
            self.assertIn(label, html)

    def test_it_calls_out_the_second_half_and_the_fourth_quarter(self):
        for period in (3, 4):
            self.fill("Michigan", "Ohio State", period, 1.0)
            self.fill("Ohio State", "Michigan", period, -1.0)
        html = self.render({"game_id": 1, "away_team": "Michigan", "home_team": "Ohio State"})
        self.assertIn("second half", html)
        self.assertIn("fourth quarter", html)

    def test_a_game_without_scored_plays_renders_nothing(self):
        self.assertEqual(self.render({"game_id": 1, "away_team": "A", "home_team": "B"}), "")


if __name__ == "__main__":
    unittest.main()
