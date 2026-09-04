"""No optional step may eat the refresh.

A heavy run took 2,580 seconds, of which local-articles took 1,800: it walked
350 Google News feeds behind eight workers, was killed by the driver's
thirty-minute cap, and stored nothing at all. Seventy per cent of the run for
zero rows, because nothing bounded it and nothing kept what it had.
"""

from __future__ import annotations

import unittest

from sports_aggregator.bootstrap import Step, steps
from sports_aggregator.social.content_cli import LOCAL_REPORTING_DEADLINE


class StepBudgetTests(unittest.TestCase):

    def _step(self, name):
        return next(step for step in steps(2026) if step.name == name)

    def test_a_step_can_carry_its_own_budget(self):
        self.assertIsNone(Step("x", "d", [], ("refresh",)).timeout_seconds)

    def test_local_reporting_is_bounded(self):
        self.assertEqual(self._step("local-articles").timeout_seconds, 600)

    def test_its_own_deadline_comes_first(self):
        """The driver's cap kills the process and loses everything it fetched.

        The step's own deadline stops waiting and keeps what arrived, so it has
        to be the one that fires.
        """
        self.assertLess(LOCAL_REPORTING_DEADLINE,
                        self._step("local-articles").timeout_seconds)

    def test_the_deadline_leaves_room_to_store_what_it_has(self):
        margin = self._step("local-articles").timeout_seconds - LOCAL_REPORTING_DEADLINE
        self.assertGreaterEqual(margin, 120, "no time to write the rows it kept")

    def test_the_driver_uses_the_step_budget_when_there_is_one(self):
        import inspect

        from sports_aggregator import scheduled_refresh

        source = inspect.getsource(scheduled_refresh._run_low_memory_phase)
        self.assertIn("step.timeout_seconds", source)


class WeatherQuotaTests(unittest.TestCase):
    """A daily allowance running out is a limit, not a fault."""

    def test_the_quota_stop_is_not_counted_as_a_failure(self):
        import inspect

        from sports_aggregator.cfb import external_cli

        source = inspect.getsource(external_cli.ingest_weather)
        self.assertIn("len(failures) - (1 if quota_exhausted else 0)", source)


if __name__ == "__main__":
    unittest.main()


class CoordinatorStepTests(unittest.TestCase):
    """The step that was never there.

    `coordinator_seasons` was empty because nothing filled it, so the matchup's
    run/pass section returned None for every team and deleted itself from the
    page, and no tempo or tendency could be attributed to a name.
    """

    def _names(self, phase, season=2026):
        return [step.name for step in steps(season) if phase in step.phases]

    def test_coordinators_are_refreshed_with_everything_else(self):
        self.assertIn("coordinators", self._names("refresh"))
        self.assertIn("coordinators", self._names("initial"))

    def test_the_history_reaches_as_far_back_as_the_tendencies_it_explains(self):
        """`team_stats` carries the attempts from RESULT_HISTORY_FLOOR, and a
        coordinator's career is the point of the measurement. The detail window
        is seven seasons, which would have stopped at 2019."""
        from sports_aggregator.bootstrap import RESULT_HISTORY_FLOOR
        history = [name for name in self._names("history")
                   if name.startswith("coordinators-history-")]
        years = sorted(int(name.rsplit("-", 1)[1]) for name in history)
        self.assertEqual(years[0], RESULT_HISTORY_FLOOR)
        self.assertEqual(years[-1], 2025)

    def test_one_encyclopedia_being_down_does_not_fail_a_refresh(self):
        step = next(s for s in steps(2026) if s.name == "coordinators")
        self.assertTrue(step.optional)
        self.assertEqual(step.timeout_seconds, 600)

    def test_it_asks_for_no_api_key_because_wikipedia_needs_none(self):
        step = next(s for s in steps(2026) if s.name == "coordinators")
        self.assertEqual(step.requires_env, ())
        self.assertEqual(step.requires_all_env, ())


class PregameSnapshotStepTests(unittest.TestCase):
    """The enrichment the scheduled refresh dropped.

    capture_due used to run on the tail of `cfb.cli sync`; the scheduled
    refresh replaced that with one subprocess per CFBD dataset and never
    carried it over, so cfb_pregame_snapshots stayed empty and "Expectation vs
    reality" had nothing to read on any game.
    """

    def _step(self):
        return next(s for s in steps(2026) if s.name == "pregame-snapshot")

    def test_it_runs_on_every_refresh_and_initial_build(self):
        names_refresh = [s.name for s in steps(2026) if "refresh" in s.phases]
        names_initial = [s.name for s in steps(2026) if "initial" in s.phases]
        self.assertIn("pregame-snapshot", names_refresh)
        self.assertIn("pregame-snapshot", names_initial)

    def test_it_lands_after_the_state_it_freezes_is_refreshed(self):
        order = [s.name for s in steps(2026)]
        for earlier in ("cfbd-sync", "cfbd-lines", "weather"):
            self.assertLess(order.index(earlier), order.index("pregame-snapshot"))

    def test_a_missed_snapshot_degrades_rather_than_fails_the_run(self):
        self.assertTrue(self._step().optional)

    def test_it_needs_no_api_key_because_it_only_reads_stored_data(self):
        step = self._step()
        self.assertEqual(step.requires_env, ())
        self.assertEqual(step.requires_all_env, ())
