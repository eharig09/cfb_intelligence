import json
from pathlib import Path
import tempfile
import unittest

from sports_aggregator.scheduled_refresh import (
    LIGHT_REFRESH_STEPS, RESULTS_REFRESH_STEPS, SCORES_REFRESH_STEPS,
    run_scheduled_refresh,
)


class ScheduledRefreshTests(unittest.TestCase):
    def test_refresh_records_history_and_releases_lock(self):
        calls = []

        def phase_runner(phase, season, **kwargs):
            calls.append((phase, season, kwargs))
            return [{"step": "cfbd-sync", "status": "success", "optional": False}]

        with tempfile.TemporaryDirectory() as directory:
            report = run_scheduled_refresh(
                2026, repo_root=directory, profile="heavy", phase_runner=phase_runner
            )
            self.assertEqual(report["status"], "success")
            self.assertEqual(report["profile"], "heavy")
            self.assertFalse((Path(directory) / "instance/scheduled_refresh.lock").exists())
            history = Path(directory) / "instance/scheduled_refresh_history.jsonl"
            self.assertEqual(json.loads(history.read_text())["season"], 2026)
            self.assertEqual(calls[0][0:2], ("refresh", 2026))
            self.assertNotIn("local-articles", calls[0][2]["only"])

    def test_light_profile_uses_light_step_allowlist_without_local_news(self):
        calls = []

        def phase_runner(phase, season, **kwargs):
            calls.append((phase, season, kwargs)); return []

        with tempfile.TemporaryDirectory() as directory:
            report = run_scheduled_refresh(
                2026, repo_root=directory, profile="light", phase_runner=phase_runner
            )
            self.assertEqual(report["status"], "success")
            self.assertEqual(calls[0][2]["only"], LIGHT_REFRESH_STEPS)
            self.assertNotIn("local-articles", LIGHT_REFRESH_STEPS)

    def test_live_profiles_have_different_box_score_costs(self):
        self.assertEqual(SCORES_REFRESH_STEPS, ["cfbd-sync", "cfbd-lines"])
        self.assertEqual(RESULTS_REFRESH_STEPS,
                         ["cfbd-sync", "cfbd-box-scores", "cfbd-lines"])

    def test_active_lock_skips_overlapping_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "instance/scheduled_refresh.lock"
            lock.parent.mkdir(); lock.write_text("{}")
            report = run_scheduled_refresh(2026, repo_root=directory)
            self.assertEqual(report["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
