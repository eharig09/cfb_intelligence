import csv
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sports_aggregator.social.local_sources import (
    _prune_geographic_outliers,
    article_matches_team,
    coverage_report,
    enrich_machine_endpoints,
    import_source_graph,
    research_registry,
    research_team,
    team_aliases,
    write_deliverables,
)


class LocalSourceResearchTests(unittest.TestCase):
    def test_ingestion_filter_requires_team_and_football_evidence(self):
        team = {"team": "Texas A&M", "aliases": ["Texas A&M football"]}
        self.assertTrue(article_matches_team(
            "Texas A&M quarterback wins the fall camp job", team))
        self.assertFalse(article_matches_team(
            "Texas A&M student organization hosts a concert", team))

    def test_ingestion_filter_rejects_named_other_sports(self):
        team = {"team": "Michigan", "aliases": ["Michigan football"]}
        self.assertFalse(article_matches_team(
            "Michigan basketball coach prepares for the Big Ten season", team))
        self.assertFalse(article_matches_team(
            "Michigan baseball transfer selected in the MLB draft", team))

    def test_other_sport_word_can_be_overridden_by_real_football_context(self):
        team = {"team": "Michigan", "aliases": ["Michigan football"]}
        self.assertTrue(article_matches_team(
            "Michigan basketball player transfers to football as a wide receiver", team))

    def test_weak_cross_state_opponent_coverage_is_removed(self):
        def source(count):
            return {"name": "Home Paper", "domain": "paper.test", "priority": 1,
                    "verification": {"result_count": count}}
        results = {
            "Home": {"team": "Home", "state": "AA", "sources": [source(20)]},
            "Opponent": {"team": "Opponent", "state": "BB", "sources": [source(2)]},
        }
        removed = _prune_geographic_outliers(results, 3)
        self.assertEqual(results["Opponent"]["sources"], [])
        self.assertEqual(removed[0]["publisher_dominant_state"], "AA")

    def test_ambiguous_programs_use_specific_aliases(self):
        self.assertEqual(team_aliases("Miami", "Hurricanes")[0],
                         "Miami Hurricanes football")
        self.assertEqual(team_aliases("Miami (OH)", "RedHawks")[0],
                         "Miami RedHawks football")

    @patch("sports_aggregator.social.local_sources._fetch_feed")
    def test_source_requires_a_domain_constrained_verification(self, fetch):
        fetch.side_effect = [
            [
                {"publisher": "Local Daily News", "domain": "localdaily.example",
                 "publisher_url": "https://localdaily.example", "headline": "Team camp",
                 "article_url": "https://news.google/a", "published": "now"},
                {"publisher": "ESPN", "domain": "espn.com",
                 "publisher_url": "https://espn.com", "headline": "National item",
                 "article_url": "https://news.google/b", "published": "now"},
            ],
            [{"publisher": "Local Daily News", "domain": "localdaily.example",
              "publisher_url": "https://localdaily.example", "headline": headline,
              "article_url": f"https://news.google/{index}", "published": "now"}
             for index, headline in enumerate(("Example practice report",
                                                "Example depth chart update"))],
        ]
        result = research_team({
            "team_id": 1, "school": "Example", "mascot": "Owls",
            "conference": "Example", "classification": "fbs",
            "city": "Example City", "state": "EX",
        }, source_limit=2)
        self.assertEqual([source["domain"] for source in result["sources"]],
                         ["localdaily.example"])
        self.assertIsNone(result["sources"][0]["native_rss"])
        self.assertIn("site:localdaily.example", result["sources"][0]["google_news_query"])

    @patch("sports_aggregator.social.local_sources.research_team")
    def test_registry_uses_database_inventory_and_writes_every_deliverable(self, research):
        research.return_value = {
            "team_id": 1, "team": "Example", "conference": "Test",
            "division": "fbs", "city": "Town", "state": "TS",
            "aliases": ["Example football"], "research_errors": [],
            "sources": [{
                "name": "Example Herald", "domain": "exampleherald.test",
                "source_type": "newspaper", "priority": 1,
                "team_specific_page": None, "native_rss": None, "sports_rss": None,
                "api_endpoint": None, "news_sitemap": None,
                "google_news_query": '"Example football" site:exampleherald.test',
                "google_news_rss": "https://news.google.test/feed", "paywall": None,
                "original_reporting": True, "confidence": "high", "notes": "verified",
                "verification": {"checked_at": "2026-08-23T00:00:00+00:00",
                                 "result_count": 5},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "cfb.sqlite3")
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE teams(team_id INTEGER,school TEXT,mascot TEXT,
                  abbreviation TEXT,conference TEXT,classification TEXT,venue_id INTEGER);
                CREATE TABLE venues(venue_id INTEGER,city TEXT,state TEXT);
                INSERT INTO teams VALUES(1,'Example','Owls','EX','Test','fbs',10);
                INSERT INTO venues VALUES(10,'Town','TS');
            """)
            connection.commit(); connection.close()
            registry = research_registry(database, max_workers=1)
            paths = write_deliverables(registry, os.path.join(directory, "output"))
            self.assertEqual(set(paths), {"registry", "publishers", "endpoints",
                                          "coverage", "problems", "summary"})
            self.assertTrue(all(path.exists() for path in paths.values()))
            payload = json.loads(paths["registry"].read_text(encoding="utf-8"))
            self.assertIn("Example", payload["teams"])
            with paths["summary"].open(encoding="utf-8") as handle:
                self.assertEqual(next(csv.DictReader(handle))["Primary Source"],
                                 "Example Herald")
            self.assertEqual(coverage_report(registry)["teams_with_only_1_source"], 1)

    @patch("sports_aggregator.social.local_sources._inspect_publisher")
    def test_declared_native_endpoints_are_projected_to_each_team_source(self, inspect):
        inspect.return_value = {
            "feeds": [{"url": "https://paper.test/sports.xml",
                       "title": "Sports", "entry_count": 20}],
            "news_sitemaps": ["https://paper.test/news-sitemap.xml"], "errors": [],
        }
        registry = {"metadata": {}, "teams": {"Example": {"sources": [{
            "domain": "paper.test", "native_rss": None, "sports_rss": None,
            "news_sitemap": None,
        }]}}}
        enrich_machine_endpoints(registry, max_workers=1)
        source = registry["teams"]["Example"]["sources"][0]
        self.assertEqual(source["sports_rss"], "https://paper.test/sports.xml")
        self.assertEqual(source["news_sitemap"],
                         "https://paper.test/news-sitemap.xml")
        self.assertEqual(coverage_report({
            "metadata": {},
            "teams": {"Example": {"team": "Example", "sources": [source]}},
        })["teams_with_verified_native_rss"], 1)

    def test_verified_registry_imports_as_team_scoped_source_graph(self):
        source = {
            "name": "Example Herald", "domain": "herald.test",
            "source_type": "newspaper", "confidence": "high",
            "google_news_rss": "https://news.google.test/example",
            "native_rss": "https://herald.test/rss.xml", "sports_rss": None,
            "news_sitemap": None,
        }
        registry = {"teams": {"Example": {
            "team_id": 1, "team": "Example", "sources": [source],
        }}}
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "sources.sqlite3")
            report = import_source_graph(registry, database)
            self.assertEqual(report, {"entities": 1, "endpoints": 2})
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute(
                "SELECT team FROM source_entity_teams").fetchone()[0], "Example")
            self.assertEqual(connection.execute(
                "SELECT COUNT(1) FROM source_endpoints WHERE verification_status='verified'"
            ).fetchone()[0], 2)
            connection.close()


if __name__ == "__main__":
    unittest.main()
