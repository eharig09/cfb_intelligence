"""The cron trigger, which is a clock and nothing more.

It runs in a container with no disk. The database mounts to the web service
alone, so nothing here can see whether a game is being played -- which is why
choosing the profile from the hour meant a score posted at 3:30pm first
appeared at 6pm. The trigger now asks for "auto" every time and the service
decides; these tests hold it to that, and to reporting honestly when the answer
was "nothing to do".
"""

from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch

from sports_aggregator.render_trigger import trigger_if_due


ENVIRONMENT = {
    "CFB_REFRESH_TIMEZONE": "America/New_York",
    "CFB_REFRESH_URL": "https://example.onrender.com/internal/cfb-refresh",
    "CFB_REFRESH_TOKEN": "test-token",
}


class _Response:
    def __init__(self, status=202, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {
            "status": "accepted", "profile": "light"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _opener(response):
    requests = []

    def opener(request, **_kwargs):
        requests.append(request)
        return response

    return opener, requests


class RenderTriggerTests(unittest.TestCase):

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_every_firing_asks_the_service_to_choose(self):
        """No hour gate here: the gate moved to where the schedule is."""
        opener, requests = _opener(_Response())
        for hour in (3, 10, 15, 21):
            with self.subTest(hour=hour):
                report = trigger_if_due(
                    now=datetime(2026, 9, 5, hour, tzinfo=timezone.utc),
                    opener=opener)
                self.assertEqual(report["requested"], "auto")
        self.assertEqual(len(requests), 4)
        self.assertIn("profile=auto", requests[0].full_url)

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_the_token_travels_with_the_request(self):
        opener, requests = _opener(_Response())
        trigger_if_due(now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-token")
        self.assertEqual(requests[0].get_method(), "POST")

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_it_reports_the_profile_the_service_actually_ran(self):
        opener, _ = _opener(_Response(payload={"status": "accepted",
                                               "profile": "scores",
                                               "reason": "games_in_progress"}))
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 20, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["status"], "triggered")
        self.assertEqual(report["profile"], "scores")

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_a_moment_calling_for_nothing_is_not_reported_as_a_refresh(self):
        """Firing every quarter hour, most firings do nothing."""
        opener, _ = _opener(_Response(status=200, payload={
            "status": "skipped", "profile": None,
            "reason": "outside_refresh_hours"}))
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 9, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["status"], "skipped")

    @patch.dict(os.environ, dict(ENVIRONMENT, CFB_REFRESH_PROFILE="heavy"),
                clear=False)
    def test_a_pinned_profile_skips_the_decision(self):
        opener, requests = _opener(_Response(payload={"status": "accepted",
                                                      "profile": "heavy"}))
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 9, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["requested"], "heavy")
        self.assertIn("profile=heavy", requests[0].full_url)

    @patch.dict(os.environ, {"CFB_REFRESH_URL": "", "CFB_REFRESH_TOKEN": ""},
                clear=False)
    def test_missing_configuration_is_an_error_rather_than_a_silent_skip(self):
        with self.assertRaises(RuntimeError):
            trigger_if_due(now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc),
                           opener=lambda *_a, **_k: _Response())

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_a_failing_endpoint_is_raised_rather_than_swallowed(self):
        opener, _ = _opener(_Response(status=500, payload={"error": "boom"}))
        with self.assertRaises(RuntimeError):
            trigger_if_due(now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc),
                           opener=opener)


if __name__ == "__main__":
    unittest.main()
