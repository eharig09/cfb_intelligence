import json
from pathlib import Path
import tempfile
import unittest

from sports_aggregator.scheduled_refresh import run_scheduled_refresh


class ScheduledRefreshTests(unittest.TestCase):
    def test_refresh_records_history_and_releases_lock(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return type("Completed", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as directory:
            report = run_scheduled_refresh(2026, repo_root=directory, runner=runner)
            self.assertEqual(report["status"], "success")
            self.assertFalse((Path(directory) / "instance/scheduled_refresh.lock").exists())
            history = Path(directory) / "instance/scheduled_refresh_history.jsonl"
            self.assertEqual(json.loads(history.read_text())["season"], 2026)
            self.assertIn("sports_aggregator.bootstrap", calls[0][0])

    def test_active_lock_skips_overlapping_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "instance/scheduled_refresh.lock"
            lock.parent.mkdir()
            lock.write_text("{}")
            report = run_scheduled_refresh(2026, repo_root=directory)
            self.assertEqual(report["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
