from datetime import datetime, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from app import _legacy_dashboards_default, create_app
from sports_aggregator.catalog import get_league
from sports_aggregator.models import Article
from sports_aggregator.service import AggregationResult


class StubService:
    def __init__(self, result):
        self.result = result

    def aggregate(self, _league):
        return self.result


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        league = get_league("college-football")
        assert league is not None
        result = AggregationResult(
            league=league,
            articles=(Article(
                title="Opening weekend preview", url="https://example.com/preview",
                source="Example Sports",
                published_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
            ),),
            errors=(),
            fetched_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        self.app = create_app({
            "TESTING": True,
            "REGISTER_LEGACY_DASHBOARDS": False,
            "LEAGUE_AGGREGATION_SERVICE": StubService(result),
            "CFB_DATABASE_PATH": os.path.join(self.temp_dir.name, "cfb.sqlite3"),
            "CFB_REFRESH_TOKEN": "test-refresh-token",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_home_and_college_football_page(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"College Football", home.data)

        page = self.client.get("/leagues/college-football/", follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        # Assert on the page's structure rather than its copy, which is edited often.
        self.assertIn(b"College Football Today", page.data)
        self.assertIn(b"Source Streams", page.data)

    def test_render_defaults_to_lightweight_cfb_runtime(self):
        with patch.dict(os.environ, {"RENDER": "true"}, clear=True):
            self.assertFalse(_legacy_dashboards_default())
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(_legacy_dashboards_default())

    def test_api_discovery_payload_and_limit(self):
        discovery = self.client.get("/api/v1/leagues")
        self.assertEqual(discovery.status_code, 200)
        self.assertEqual(discovery.get_json()["leagues"][0]["slug"], "college-football")

        response = self.client.get("/api/v1/leagues/college-football/articles?limit=1")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["articles"][0]["source"], "Example Sports")

    def test_unknown_league_returns_404(self):
        self.assertEqual(self.client.get("/leagues/not-a-league/").status_code, 404)
        self.assertEqual(
            self.client.get("/api/v1/leagues/not-a-league/articles").status_code, 404,
        )

    def test_source_graph_routes(self):
        response = self.client.get("/api/v1/cfb/source-entities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["entity_count"], 0)
        self.assertEqual(self.client.get("/college-football/admin/source-graph/").status_code, 200)
        content = self.client.get("/api/v1/cfb/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.get_json()["count"], 0)

    @patch("app.subprocess.Popen")
    def test_render_refresh_hook_requires_token_and_starts_background_job(self, popen):
        self.assertEqual(self.client.post("/internal/cfb-refresh").status_code, 401)
        response = self.client.post(
            "/internal/cfb-refresh",
            headers={"Authorization": "Bearer test-refresh-token"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "accepted")
        command = popen.call_args.args[0]
        self.assertIn("sports_aggregator.scheduled_refresh", command)


if __name__ == "__main__":
    unittest.main()
