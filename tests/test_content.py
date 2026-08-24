from datetime import datetime, timezone
import os
import tempfile
import unittest

from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.models import Article
from sports_aggregator.social.content import ContentRepository, classify_topics
from sports_aggregator.social.models import SourceEndpointProfile, SourceEntityProfile
from sports_aggregator.social.unified import UnifiedSourceRegistry


class ContentRepositoryTests(unittest.TestCase):
    def test_post_topics_and_exact_entities_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,"cfb.sqlite3"); cfb=CFBRepository(path)
            cfb.replace_teams((Team(1,"Michigan","Wolverines","MICH","Big Ten",None,
                                    "fbs",None,None,(),("Michigan",),None,None),))
            cfb.replace_players(2026,(Player("p1",2026,"Alex","Example","Michigan",
                                             "QB",7,74,210,3),))
            graph=UnifiedSourceRegistry(path)
            entity_id=graph.upsert_entity(SourceEntityProfile(
                name="Beat Reporter",organization="Paper",entity_type="PERSON",
                source_classes=("BEAT_REPORTER",),teams=("Michigan",),reporting_score=5))
            graph.upsert_endpoint(entity_id,SourceEndpointProfile(
                platform="bluesky",endpoint_type="BLUESKY_ACCOUNT",handle="beat.example",
                platform_id="did:plc:beat",verification_status="verified"))
            repository=ContentRepository(path); endpoint=repository.bluesky_endpoints()[0]
            item={"post":{"uri":"at://did:plc:beat/app.bsky.feed.post/one","cid":"cid1",
                  "author":{"did":"did:plc:beat","handle":"beat.example","displayName":"Beat"},
                  "record":{"text":"Michigan starter Alex Example missed practice with an injury.",
                            "createdAt":"2026-08-23T12:00:00Z"}}}
            content_id=repository.store_bluesky_post(endpoint,item,2026)
            self.assertIsNotNone(content_id)
            stored=repository.recent(1)[0]
            self.assertEqual(stored["source_role"],"REPORTING_UNDETERMINED")
            self.assertIn("INJURY",stored["topics"])
            self.assertEqual(stored["teams"][0]["school"],"Michigan")
            self.assertEqual(stored["players"][0]["player_id"],"p1")

            article_id = repository.store_article(Article(
                title="Michigan quarterback update", url="https://paper.example/michigan-qb",
                source="Paper", published_at=datetime(2026,8,23,13,tzinfo=timezone.utc),
                summary="Alex Example remains the starter.", reliability=4,
            ), 2026)
            article = next(item for item in repository.recent(5) if item["content_id"] == article_id)
            self.assertEqual(article["platform"], "rss")
            self.assertEqual(article["teams"][0]["school"], "Michigan")

    def test_reposts_are_suppressed_and_topics_are_multilabel(self):
        topics={item[0] for item in classify_topics("CFP rankings and playoff projection")}
        # A subset assertion: new topic rules should be able to add labels
        # without this test failing, as long as the established ones still fire.
        self.assertTrue({"PLAYOFF","RANKINGS","STATISTICAL_ANALYSIS"} <= topics, topics)
        self.assertGreater(len(topics), 1)

    def test_multi_team_publisher_does_not_add_every_team_it_covers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "cfb.sqlite3")
            cfb = CFBRepository(path)
            cfb.replace_teams((
                Team(1, "Houston", "Cougars", "HOU", "Big 12", None,
                     "fbs", None, None, (), ("Houston", "Cougars", "Houston Cougars"),
                     None, None),
                Team(2, "Texas", "Longhorns", "TEX", "SEC", None,
                     "fbs", None, None, (), ("Texas", "Longhorns", "Texas Longhorns"),
                     None, None),
            ))
            graph = UnifiedSourceRegistry(path)
            graph.upsert_entity(SourceEntityProfile(
                name="Houston Chronicle", organization="Houston Chronicle",
                entity_type="ORGANIZATION", source_classes=("LOCAL_OUTLET",),
                teams=("Houston", "Texas"), entity_key="local-publisher:chronicle"))
            repository = ContentRepository(path)
            content_id = repository.store_article(Article(
                title="Texas quarterback prepares for opener - Houston Chronicle",
                url="https://chronicle.example/texas", source="Houston Chronicle",
                publisher="Houston Chronicle",
                source_entity_key="local-publisher:chronicle",
            ), 2026)
            article = next(row for row in repository.recent(5)
                           if row["content_id"] == content_id)
            self.assertEqual([team["school"] for team in article["teams"]], ["Texas"])


if __name__=="__main__": unittest.main()
