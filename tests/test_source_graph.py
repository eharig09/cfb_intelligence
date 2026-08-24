from types import SimpleNamespace
import os
import tempfile
import unittest

from sports_aggregator.providers.reddit import RedditCommunityProvider
from sports_aggregator.social.models import SourceEndpointProfile, SourceEntityProfile
from sports_aggregator.social.unified import UnifiedSourceRegistry


class SourceGraphTests(unittest.TestCase):
    def test_configured_sources_include_verified_ncaa_fbs_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = UnifiedSourceRegistry(os.path.join(directory, "sources.sqlite3"))
            registry.seed_configured_endpoints()
            ncaa = next(entity for entity in registry.list_entities()
                        if entity["entity_key"] == "organization:ncaa")
            endpoint = ncaa["endpoints"][0]
            self.assertEqual(endpoint["platform"], "rss")
            self.assertEqual(endpoint["verification_status"], "verified")
            self.assertIn("football/fbs/rss.xml", endpoint["url"])
            yahoo = next(entity for entity in registry.list_entities()
                         if entity["entity_key"] == "organization:yahoo-sports")
            self.assertEqual(yahoo["endpoints"][0]["verification_status"], "verified")
            self.assertIn("college-football/rss", yahoo["endpoints"][0]["url"])

    def test_one_entity_can_own_multiple_platform_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = UnifiedSourceRegistry(os.path.join(directory, "sources.sqlite3"))
            entity_id = registry.upsert_entity(SourceEntityProfile(
                name="Example Show", organization=None, entity_type="SHOW",
                source_classes=("PODCAST", "YOUTUBE_SHOW"), analytics_score=4,
            ))
            registry.upsert_endpoint(entity_id, SourceEndpointProfile(
                platform="youtube", endpoint_type="YOUTUBE_CHANNEL", platform_id="channel-1"))
            registry.upsert_endpoint(entity_id, SourceEndpointProfile(
                platform="rss", endpoint_type="PODCAST_RSS", url="https://example.com/feed.xml"))
            status = registry.status()
            self.assertEqual(status["entity_count"], 1)
            self.assertEqual(status["endpoint_count"], 2)
            self.assertEqual(set(status["entities"][0]["classes"]), {"PODCAST", "YOUTUBE_SHOW"})

    def test_reddit_link_credits_external_publisher(self):
        submission = SimpleNamespace(
            title="Local report", url="https://localpaper.example/cfb/story",
            is_self=False, link_flair_text="News", created_utc=1_787_488_000,
            author=SimpleNamespace(name="submitter"),
        )
        provider = RedditCommunityProvider("CFB", loader=lambda _name, _limit: [submission])
        article = provider.fetch()[0]
        self.assertEqual(article.source, "localpaper.example")
        self.assertEqual(article.discovered_via, "r/CFB")
        self.assertEqual(article.author, "")
        self.assertEqual(article.content_kind, "LINK_DISCOVERY")
        self.assertEqual(article.discovery_endpoint_key, "reddit:subreddit:cfb")


if __name__ == "__main__":
    unittest.main()
