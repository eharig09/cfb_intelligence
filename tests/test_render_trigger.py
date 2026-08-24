from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from sports_aggregator.render_trigger import trigger_if_due


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"status":"accepted"}'


class RenderTriggerTests(unittest.TestCase):
    @patch.dict(os.environ, {"CFB_REFRESH_TIMEZONE": "America/New_York",
                            "CFB_REFRESH_HOURS": "6,12,18,23"}, clear=False)
    def test_non_scheduled_hour_does_not_call_endpoint(self):
        called = []
        report = trigger_if_due(
            now=datetime(2026, 8, 23, 15, tzinfo=timezone.utc),
            opener=lambda *_args, **_kwargs: called.append(True),
        )
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(called, [])

    @patch.dict(os.environ, {
        "CFB_REFRESH_TIMEZONE": "America/New_York", "CFB_REFRESH_HOURS": "6,12,18,23",
        "CFB_REFRESH_URL": "https://example.onrender.com/internal/cfb-refresh",
        "CFB_REFRESH_TOKEN": "test-token",
    }, clear=False)
    def test_scheduled_hour_posts_authenticated_request(self):
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return _Response()

        report = trigger_if_due(
            now=datetime(2026, 8, 23, 10, tzinfo=timezone.utc), opener=opener)
        self.assertEqual(report["status"], "triggered")
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-token")


if __name__ == "__main__":
    unittest.main()
