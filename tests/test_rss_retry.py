"""A 503 from a feed aggregator means come back, not go away.

Local reporting walks 350 Google News searches. On a home connection 312 of
them succeed; on the instance 268 of 350 came back 503, because the aggregator
throttles datacenter traffic harder. There was no retry at all -- one attempt,
raise_for_status, recorded as an error -- so the throttle was being treated as
a permanent failure of the feed.
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from sports_aggregator.models import FeedConfig
from sports_aggregator.providers.rss import (
    RETRY_BACKOFF, RETRY_STATUSES, RSSNewsProvider, _retrying_session,
)
from sports_aggregator.social.content_cli import LOCAL_REPORTING_WORKERS


FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>Test</title><link>https://example.test/</link><description>d</description>
<item><title>A local story</title><link>https://example.test/1</link>
<description>Body</description><pubDate>Wed, 26 Aug 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""


class _Throttling(BaseHTTPRequestHandler):
    """Refuses the first two requests the way Google News refuses a burst."""

    refusals = 2
    seen = 0

    def do_GET(self):
        type(self).seen += 1
        if type(self).seen <= type(self).refusals:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"busy")
            return
        body = FEED.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class RetryPolicyTests(unittest.TestCase):

    def test_the_statuses_worth_retrying_include_the_one_we_saw(self):
        self.assertIn(503, RETRY_STATUSES)
        self.assertIn(429, RETRY_STATUSES, "the other way a throttle is spelled")

    def test_the_session_is_shared_so_feeds_reuse_connections(self):
        self.assertIs(_retrying_session(), _retrying_session())

    def test_the_backoff_is_long_enough_to_outlast_a_burst_limit(self):
        self.assertGreaterEqual(RETRY_BACKOFF, 1.0)

    def test_fewer_feeds_are_fetched_at_once_than_before(self):
        """Eight was what got 268 of 350 refused."""
        self.assertLess(LOCAL_REPORTING_WORKERS, 8)


class ThrottledFeedTests(unittest.TestCase):
    """The whole point, end to end against a server that behaves like one."""

    def setUp(self):
        _Throttling.seen = 0
        self.server = HTTPServer(("127.0.0.1", 0), _Throttling)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/rss" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _provider(self, session):
        return RSSNewsProvider(
            FeedConfig(name="Local", url=self.url, max_articles=5,
                       source_type="local_reporting", reliability=4),
            session=session)

    def test_a_feed_that_refuses_twice_is_still_read(self):
        session = requests.Session()
        session.mount("http://", _retrying_session().get_adapter("https://x/"))
        articles = self._provider(session).fetch()
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "A local story")
        self.assertEqual(_Throttling.seen, 3, "two refusals then the feed")

    def test_without_retries_the_same_feed_is_lost(self):
        """Which is what was happening to 268 feeds a run."""
        plain = requests.Session()
        with self.assertRaises(Exception):
            self._provider(plain).fetch()


if __name__ == "__main__":
    unittest.main()
