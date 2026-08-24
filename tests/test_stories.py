import os
import tempfile
import unittest

from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.models import SourceEndpointProfile,SourceEntityProfile
from sports_aggregator.social.stories import (
    StoryRepository,
    _build_clusters,
    _canonical_url,
    _external_article_url,
    _tokens,
)
from sports_aggregator.social.unified import UnifiedSourceRegistry


class StoryRepositoryTests(unittest.TestCase):
    @staticmethod
    def cluster_item(index, *, source, platform="rss", url="", text="Michigan injury update"):
        return {
            "platform": platform,
            "platform_content_id": str(index),
            "source_entity_id": source,
            "original_url": url,
            "published_at": "2026-08-23T12:00:00+00:00",
            "teams": {1},
            "players": set(),
            "games": set(),
            "topics": {"INJURY"},
            "tokens": _tokens(text),
        }

    def test_canonical_url_preserves_identity_and_strips_tracking(self):
        first = _canonical_url("https://www.youtube.com/watch?v=abc123&utm_source=x")
        second = _canonical_url("https://youtube.com/watch?v=xyz789")
        self.assertEqual(first, "https://youtube.com/watch?v=abc123")
        self.assertEqual(second, "https://youtube.com/watch?v=xyz789")
        self.assertNotEqual(first, second)
        self.assertEqual(
            _canonical_url("http://paper.example/read?edition=west&utm_campaign=fall"),
            "https://paper.example/read?edition=west",
        )

    def test_platform_permalink_is_not_an_external_article(self):
        self.assertEqual(_external_article_url({
            "platform": "youtube",
            "original_url": "https://youtube.com/watch?v=abc123",
        }), "")
        self.assertEqual(_external_article_url({
            "platform": "bluesky",
            "original_url": "https://bsky.app/profile/reporter/post/one",
        }), "")
        self.assertEqual(_external_article_url({
            "platform": "bluesky",
            "original_url": "https://paper.example/story/one?utm_source=bsky",
        }), "https://paper.example/story/one")

    def test_different_article_urls_can_merge_across_sources(self):
        items = [
            self.cluster_item(1, source=1, url="https://local.example/michigan-update",
                              text="Michigan injury update quarterback misses practice"),
            self.cluster_item(2, source=2, url="https://national.example/cfb/michigan-qb",
                              text="Michigan quarterback injury update after missed practice"),
        ]
        clusters = _build_clusters(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0][1], "SHARED_TEAM_TOPIC")
        self.assertEqual(len(clusters[0][2]), 2)

    def test_similar_items_from_one_source_do_not_merge(self):
        items = [
            self.cluster_item(1, source=1, platform="youtube",
                              url="https://youtube.com/watch?v=one",
                              text="Michigan preseason win total prediction"),
            self.cluster_item(2, source=1, platform="youtube",
                              url="https://youtube.com/watch?v=two",
                              text="Michigan preseason win total projections"),
        ]
        clusters = _build_clusters(items)
        self.assertEqual(len(clusters), 2)

    def test_repeated_source_homepage_is_not_shared_article(self):
        items = [
            self.cluster_item(index, source=7, platform="podcast",
                              url="https://show.example/episodes",
                              text=f"Unrelated episode subject number {index}")
            for index in range(4)
        ]
        clusters = _build_clusters(items)
        self.assertEqual(len(clusters), 4)
        self.assertTrue(all(method == "SINGLE_ITEM" for _, method, _ in clusters))

    def test_exact_external_url_clusters_and_roles_earliest_report_as_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,"cfb.sqlite3"); cfb=CFBRepository(path)
            cfb.replace_teams((Team(1,"Michigan",None,"MICH","Big Ten",None,"fbs",None,None,(),("Michigan",),None,None),))
            graph=UnifiedSourceRegistry(path)
            endpoints=[]
            for index,name in enumerate(("Reporter One","Reporter Two"),1):
                entity=graph.upsert_entity(SourceEntityProfile(name=name,organization=None,entity_type="PERSON",
                    source_classes=("NATIONAL_REPORTER",),reliability_score=5,reporting_score=5))
                graph.upsert_endpoint(entity,SourceEndpointProfile(platform="bluesky",endpoint_type="BLUESKY_ACCOUNT",
                    handle=f"r{index}.example",platform_id=f"did:plc:r{index}",verification_status="verified"))
            content=ContentRepository(path); endpoints=content.bluesky_endpoints()
            for index,endpoint in enumerate(endpoints):
                post={"post":{"uri":f"at://{endpoint['platform_id']}/app.bsky.feed.post/{index}","cid":f"c{index}",
                    "author":{"did":endpoint["platform_id"],"handle":endpoint["handle"]},
                    "record":{"text":"Michigan injury update https://paper.example/story",
                              "createdAt":f"2026-08-23T1{index}:00:00Z",
                              "facets":[{"features":[{"uri":"https://paper.example/story"}]}]}}}
                content.store_bluesky_post(endpoint,post,2026)
            report=StoryRepository(path).rebuild(lookback_days=365)
            self.assertEqual(report["stories"],1); self.assertEqual(report["multi_item_stories"],1)
            story=StoryRepository(path).list_stories()[0]
            self.assertEqual(story["clustering_method"],"SHARED_ARTICLE")
            self.assertEqual(story["sources"][0]["source_role"],"ORIGINAL_REPORT_CANDIDATE")
            self.assertEqual(story["title"], "Michigan injury update https://paper.example/story")
            self.assertEqual(story["url"], "https://paper.example/story")


if __name__=="__main__": unittest.main()
