import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from app import create_app
from sports_aggregator.cfb import passing_plays
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.passing_plays import (
    MIN_GAME_ATTEMPTS, coverage, game_splits, store_attempts, sync_season,
    sync_week, team_season_splits,
)
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


def attempt(play_id, offense, defense, direction, *, depth="short", epa=None,
            air=8.0, yac=3.0, outcome="completion", week=9, game_id=1):
    return {"playId": play_id, "gameId": game_id, "season": 2025, "week": week,
            "offense": offense, "defense": defense, "passer": "A Passer",
            "passerId": "p1", "target": "A Target", "targetId": "t1",
            "down": 1, "distance": 10, "startYardsToGoal": 60,
            "passDirection": direction, "passDepth": depth,
            "passLocation": None if direction is None else f"{depth} {direction}",
            "airYards": air, "yardsAfterCatch": yac, "totalYards": 11,
            "outcome": outcome, "parseStatus": "complete", "_epa": epa}


def score_plays(path, pairs):
    """Give plays an ep-v2 EPA, through the real schema rather than a stand-in."""
    now = "2026-01-01T00:00:00+00:00"
    with closing(sqlite3.connect(path)) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO cfb_play_epa"
            "(play_id,model_version,epa,possession_changed,scored_at) VALUES (?,?,?,0,?)",
            [(play_id, "ep-v2", epa, now) for play_id, epa in pairs])
        connection.commit()


def mark_garbage_time(path, play_id):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO cfb_play_metrics"
            "(play_id,metric_version,garbage_time,derived_at) VALUES (?,?,1,?)",
            (play_id, "pbp-v1", "2026-01-01T00:00:00+00:00"))
        connection.commit()


class FakeClient:
    def __init__(self, by_week): self.by_week = by_week; self.calls = []

    def get(self, path, params, **kwargs):
        self.calls.append((path, params.get("week")))
        week = params.get("week")
        if week in self.by_week and self.by_week[week] == "boom":
            raise RuntimeError("provider is unwell")
        return self.by_week.get(week, [])


class PassingStoreTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_attempts_are_stored_with_their_direction(self):
        report = store_attempts(self.repository, [
            attempt("1", "A", "B", "middle"), attempt("2", "A", "B", "left")])
        self.assertEqual(report["stored"], 2)
        self.assertEqual(report["classified"], 2)

    def test_an_unclassified_attempt_is_kept_but_not_counted_as_classified(self):
        """Coverage is partial, and a caller has to be able to see how partial."""
        report = store_attempts(self.repository, [
            attempt("1", "A", "B", "middle"), attempt("2", "A", "B", None)])
        self.assertEqual(report["stored"], 2)
        self.assertEqual(report["classified"], 1)
        self.assertEqual(report["coverage"], 0.5)

    def test_resyncing_a_week_corrects_rows_rather_than_duplicating_them(self):
        store_attempts(self.repository, [attempt("1", "A", "B", "left")])
        store_attempts(self.repository, [attempt("1", "A", "B", "middle")])
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT play_id, pass_direction FROM cfbd_passing_plays").fetchall()
        self.assertEqual(rows, [("1", "middle")])

    def test_a_row_without_the_teams_is_skipped_rather_than_stored_half_formed(self):
        broken = attempt("1", "A", "B", "middle"); broken["offense"] = None
        self.assertEqual(store_attempts(self.repository, [broken])["stored"], 0)

    def test_one_bad_week_does_not_lose_the_others(self):
        client = FakeClient({1: [attempt("1", "A", "B", "middle")], 2: "boom",
                             3: [attempt("3", "A", "B", "left")]})
        report = sync_season(self.repository, client, season=2025, weeks=(1, 2, 3))
        self.assertEqual(report["stored"], 2)
        self.assertEqual([f["week"] for f in report["failures"]], [2])

    def test_sync_week_reports_what_it_stored(self):
        client = FakeClient({9: [attempt("1", "A", "B", "middle")]})
        report = sync_week(self.repository, client, season=2025, week=9)
        self.assertEqual((report["season"], report["week"], report["stored"]), (2025, 9, 1))

    def test_coverage_describes_the_season(self):
        store_attempts(self.repository, [
            attempt("1", "A", "B", "middle"), attempt("2", "A", "B", None)])
        report = coverage(self.repository, 2025)
        self.assertEqual((report["attempts"], report["classified"]), (2, 1))
        self.assertEqual(report["games"], 1)


class PassingSplitTests(unittest.TestCase):
    """The splits are the point: middle against outside, thrown and allowed."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        passing_plays.initialize(self.repository)
        self.attempts = []
        # Middle is worth more than outside, which is the shape of the real data.
        for index in range(6):
            self.attempts.append(attempt(f"m{index}", "Michigan", "Ohio State", "middle"))
        for index in range(10):
            self.attempts.append(attempt(f"o{index}", "Michigan", "Ohio State", "left"))
        store_attempts(self.repository, self.attempts)
        score_plays(self.path, [(f"m{i}", 1.0) for i in range(6)]
                    + [(f"o{i}", 0.1) for i in range(10)])

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_the_middle_and_the_outside_are_reported_separately(self):
        splits = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        self.assertEqual(splits["middle"]["attempts"], 6)
        self.assertEqual(splits["outside"]["attempts"], 10)
        self.assertAlmostEqual(splits["middle"]["epa_per_attempt"], 1.0)
        self.assertAlmostEqual(splits["outside"]["epa_per_attempt"], 0.1)

    def test_left_and_right_are_one_bucket(self):
        store_attempts(self.repository, [attempt("r0", "Michigan", "Ohio State", "right")])
        score_plays(self.path, [("r0", 0.1)])
        splits = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        self.assertEqual(splits["outside"]["attempts"], 11)

    def test_what_one_team_throws_is_what_the_other_allows(self):
        offense = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        defense = team_season_splits(self.repository, "Ohio State", 2025)["defense"]
        self.assertEqual(offense["middle"]["attempts"], defense["middle"]["attempts"])
        self.assertAlmostEqual(offense["middle"]["epa_per_attempt"],
                               defense["middle"]["epa_per_attempt"])

    def test_the_middle_share_is_reported(self):
        splits = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        self.assertAlmostEqual(splits["middle_share"], 6 / 16)

    def test_garbage_time_is_excluded(self):
        mark_garbage_time(self.path, "m0")
        splits = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        self.assertEqual(splits["middle"]["attempts"], 5)

    def test_an_unscored_attempt_contributes_nothing(self):
        """No EPA row means no EPA, not a zero dragging the average down."""
        store_attempts(self.repository, [attempt("x0", "Michigan", "Ohio State", "middle")])
        splits = team_season_splits(self.repository, "Michigan", 2025)["offense"]
        self.assertEqual(splits["middle"]["attempts"], 6)

    def test_a_game_reports_both_teams_from_both_sides(self):
        splits = game_splits(self.repository, 1)
        self.assertEqual(set(splits), {"Michigan", "Ohio State"})
        self.assertEqual(splits["Michigan"]["offense"]["middle"]["attempts"], 6)
        self.assertEqual(splits["Ohio State"]["defense"]["middle"]["attempts"], 6)

    def test_a_game_with_nothing_stored_is_empty_not_an_error(self):
        self.assertEqual(game_splits(self.repository, 99999), {})


class PassingRenderTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd({
            "id": i, "school": s, "mascot": s, "abbreviation": s[:3].upper(),
            "alternateNames": [], "conference": "Big Ten", "classification": "fbs",
            "color": "#0033A0", "logos": []}) for i, s in ((1, "Michigan"), (2, "Ohio State"))])
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DATABASE_PATH": self.path})

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def render(self, name, game):
        with self.app.test_request_context("/"):
            return str(self.app.jinja_env.globals[name](game))

    def test_a_game_without_passing_detail_says_so_rather_than_vanishing(self):
        html = self.render("passing_game_splits", {"game_id": 1, "season": 2025})
        self.assertIn("Middle of the field", html)
        self.assertIn("CFBD publishes pass direction", html)

    def test_a_matchup_without_passing_detail_says_so_too(self):
        html = self.render("passing_matchup_splits", {
            "season": 2025, "away_team": "Michigan", "home_team": "Ohio State"})
        self.assertIn("Middle of the field", html)
        self.assertIn("CFBD publishes pass direction", html)

    def test_a_matchup_missing_its_teams_renders_nothing_at_all(self):
        self.assertEqual(self.render("passing_matchup_splits", {"season": 2025}), "")

    def test_the_block_discloses_that_it_is_not_a_recommendation(self):
        """The middle EPA gap is a description of available value, not advice."""
        passing_plays.initialize(self.repository)
        store_attempts(self.repository, [
            attempt(f"m{i}", "Michigan", "Ohio State", "middle") for i in range(5)])
        score_plays(self.path, [(f"m{i}", 1.0) for i in range(5)])
        html = self.render("passing_matchup_splits", {
            "season": 2025, "away_team": "Michigan", "home_team": "Ohio State"})
        self.assertIn("recommendation", html)
        self.assertIn("Middle", html)


class RefreshWiringTests(unittest.TestCase):
    def test_the_analytics_pipeline_has_scheduled_steps(self):
        """It had none, which is why the whole layer was empty."""
        from sports_aggregator.bootstrap import steps
        by_name = {step.name: step for step in steps(2026)}
        for name in ("pbp", "pbp-derive", "epa", "team-advanced",
                     "win-probability", "passing-detail"):
            self.assertIn(name, by_name, f"{name} has no scheduled step")
            self.assertIn("refresh", by_name[name].phases)

    def test_the_epa_step_builds_the_version_the_report_reads(self):
        from sports_aggregator.bootstrap import steps
        from sports_aggregator.cfb.team_game_advanced import MODEL_VERSION
        command = {step.name: step.command for step in steps(2026)}["team-advanced"]
        self.assertIn(MODEL_VERSION, command)


if __name__ == "__main__":
    unittest.main()
