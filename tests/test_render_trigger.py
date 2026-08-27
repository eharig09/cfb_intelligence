"""The Render cron trigger asks the web service to choose the smallest refresh."""

from datetime import datetime, timezone
from urllib.error import HTTPError
import io
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
        opener, requests = _opener(_Response())
        for hour in (3, 10, 15, 21):
            report = trigger_if_due(
                now=datetime(2026, 9, 5, hour, tzinfo=timezone.utc), opener=opener)
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
        opener, _ = _opener(_Response(status=200, payload={
            "status": "skipped", "profile": None,
            "reason": "outside_refresh_hours"}))
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 9, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["status"], "skipped")

    @patch.dict(os.environ, dict(ENVIRONMENT, CFB_REFRESH_PROFILE="heavy"), clear=False)
    def test_a_pinned_profile_skips_the_decision(self):
        opener, requests = _opener(_Response(payload={"status": "accepted", "profile": "heavy"}))
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 9, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["requested"], "heavy")
        self.assertIn("profile=heavy", requests[0].full_url)

    @patch.dict(os.environ, {"CFB_REFRESH_URL": "", "CFB_REFRESH_TOKEN": ""}, clear=False)
    def test_missing_configuration_is_an_error(self):
        with self.assertRaises(RuntimeError):
            trigger_if_due(now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc),
                           opener=lambda *_a, **_k: _Response())

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_retryable_503_can_recover(self):
        calls = []
        responses = [_Response(status=503, payload={"error": "deploying"}), _Response()]

        def opener(_request, **_kwargs):
            calls.append(True)
            return responses.pop(0)

        sleeps = []
        report = trigger_if_due(
            now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc), opener=opener,
            sleeper=sleeps.append, attempts=2)
        self.assertEqual(report["status"], "triggered")
        self.assertEqual(report["attempt"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [2.0])

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_http_error_body_is_preserved(self):
        def opener(_request, **_kwargs):
            raise HTTPError(
                "https://example.onrender.com", 503, "Service Unavailable", {},
                io.BytesIO(b'{"error":"instance restarting"}'))

        with self.assertRaisesRegex(RuntimeError, "instance restarting"):
            trigger_if_due(
                now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc), opener=opener,
                sleeper=lambda _seconds: None, attempts=2)

    @patch.dict(os.environ, ENVIRONMENT, clear=False)
    def test_non_retryable_auth_failure_fails_immediately(self):
        calls = []

        def opener(_request, **_kwargs):
            calls.append(True)
            return _Response(status=401, payload={"error": "bad token"})

        with self.assertRaises(RuntimeError):
            trigger_if_due(
                now=datetime(2026, 9, 5, 18, tzinfo=timezone.utc), opener=opener,
                sleeper=lambda _seconds: None)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
