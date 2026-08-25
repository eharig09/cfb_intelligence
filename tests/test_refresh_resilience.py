"""Two failures from the first refresh that actually completed on Render.

The run finished degraded rather than dead — parent peak 103 MB, heaviest child
272.9 MB — so the memory ceiling and the lock reclamation held. What it exposed
was different: a step that could not start threads despite having memory to
spare, and a step that spent ten of the refresh's seventeen minutes retrying a
limit that resets tomorrow.
"""

from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest

import requests

from sports_aggregator.providers.weather import (
    DAILY_LIMIT_MARKERS, OpenMeteoClient, WeatherQuotaExhausted,
)
from sports_aggregator.social.content_cli import (
    WORKER_STACK_BYTES, _use_small_thread_stacks,
)


class ThreadStackTests(unittest.TestCase):
    """RLIMIT_AS counts address space, and thread stacks are address space.

    `local-articles` died on "can't start new thread" while resident memory sat
    at 272.9 MB, well inside the 320 MB ceiling. glibc reserves 8 MB of address
    space per thread stack, so an eight-worker pool asked for 64 MB the ceiling
    had already accounted for.
    """

    def tearDown(self):
        threading.stack_size(0)

    def test_workers_reserve_far_less_than_the_default(self):
        _use_small_thread_stacks()
        self.assertEqual(threading.stack_size(), WORKER_STACK_BYTES)
        # Eight workers now reserve 4 MB rather than 64 MB.
        self.assertLess(WORKER_STACK_BYTES * 8, 8 * 1024 * 1024)

    def test_threads_still_run_with_the_smaller_stack(self):
        _use_small_thread_stacks()
        seen = []
        worker = threading.Thread(target=lambda: seen.append(True))
        worker.start()
        worker.join()
        self.assertEqual(seen, [True])

    def test_a_platform_that_refuses_the_size_is_tolerated(self):
        original = threading.stack_size

        def refuse(_size=None):
            raise ValueError("size not supported")

        threading.stack_size = refuse
        try:
            _use_small_thread_stacks()   # must not raise
        finally:
            threading.stack_size = original


class _Response:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        raise requests.HTTPError(str(self.status_code))

    def json(self):
        return {}


class _Session:
    def __init__(self, response):
        self.response, self.headers = response, {}
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


class WeatherQuotaTests(unittest.TestCase):
    """A spent daily allowance is not a transient rate limit."""

    def _client(self, status: int, body: str):
        session = _Session(_Response(status, body))
        client = OpenMeteoClient(
            cache_path=pathlib.Path(tempfile.mkdtemp()), session=session)
        return client, session

    def test_the_daily_allowance_raises_its_own_error(self):
        client, _ = self._client(
            429, '{"error":true,"reason":"Daily API request limit exceeded. '
                 'Please try again tomorrow."}')
        with self.assertRaises(WeatherQuotaExhausted):
            client.venue_forecast(34.014, -118.288)

    def test_a_burst_limit_is_still_treated_as_transient(self):
        """Retrying a minutely limit works; retrying a daily one cannot."""
        client, _ = self._client(
            429, '{"error":true,"reason":"Minutely API request limit exceeded."}')
        with self.assertRaises(RuntimeError) as raised:
            client.venue_forecast(34.014, -118.288)
        self.assertNotIsInstance(raised.exception, WeatherQuotaExhausted)

    def test_other_upstream_failures_are_unaffected(self):
        client, _ = self._client(503, "upstream unavailable")
        with self.assertRaises(RuntimeError) as raised:
            client.venue_forecast(34.014, -118.288)
        self.assertNotIsInstance(raised.exception, WeatherQuotaExhausted)

    def test_the_marker_matches_the_wording_render_actually_returned(self):
        observed = ("Daily API request limit exceeded. Please try again tomorrow.")
        self.assertTrue(any(marker in observed.casefold()
                            for marker in DAILY_LIMIT_MARKERS))

    def test_the_error_carries_the_upstream_reason(self):
        client, _ = self._client(
            429, '{"error":true,"reason":"Daily API request limit exceeded."}')
        with self.assertRaises(WeatherQuotaExhausted) as raised:
            client.venue_forecast(34.014, -118.288)
        self.assertIn("daily quota exhausted", str(raised.exception).casefold())


if __name__ == "__main__":
    unittest.main()
