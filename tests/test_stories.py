import os
import tempfile
import unittest

from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.models import SourceEndpointProfile,SourceEntityProfile
from sports_aggregator.social.stories import StoryRepository
from sports_aggregator.social.unified import UnifiedSourceRegistry


class StoryRepositoryTests(unittest.TestCase):
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
            self.assertEqual(story["clustering_method"],"EXACT_EXTERNAL_URL")
            self.assertEqual(story["sources"][0]["source_role"],"ORIGINAL_REPORT_CANDIDATE")
            self.assertEqual(story["title"], "Michigan injury update https://paper.example/story")
            self.assertEqual(story["url"], "https://paper.example/story")


if __name__=="__main__": unittest.main()
