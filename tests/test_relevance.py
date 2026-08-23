from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest

from sports_aggregator.cfb.matchups import (
    MatchupSignal, game_matchup_report, rank_matchups, score_matchup,
)
from sports_aggregator.social.content import (
    ContentRepository, links_externally, reddit_content_type,
)
from sports_aggregator.social.relevance import (
    ROLE_WEIGHT, recency_factor, score_item, topic_profile,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


BEAT_WRITER = {"reliability_score": 5, "team_access_score": 5, "national_score": 2,
               "reporting_score": 5, "breaking_score": 4, "analytics_score": 2,
               "scheme_score": 2, "draft_score": 1, "recruiting_score": 2,
               "transfer_score": 2, "awards_score": 1, "g5_score": 1, "official_score": 0}

PUNDIT = {"reliability_score": 3, "team_access_score": 1, "national_score": 4,
          "reporting_score": 2, "breaking_score": 2, "analytics_score": 2,
          "scheme_score": 2, "draft_score": 1, "recruiting_score": 1,
          "transfer_score": 1, "awards_score": 1, "g5_score": 1, "official_score": 0}


class RelevanceTests(unittest.TestCase):
    def test_topic_profile_prefers_the_most_important_topic(self):
        topic, weight, halflife = topic_profile(["MEDIA", "INJURY", "CONFERENCE"])
        self.assertEqual(topic, "INJURY")
        self.assertEqual(halflife, 3.0)
        self.assertGreater(weight, 0.9)

    def test_unknown_topics_fall_back_without_raising(self):
        topic, weight, _ = topic_profile(["SOMETHING_NEW"])
        self.assertIsNone(topic)
        self.assertEqual(weight, 0.45)

    def test_recency_halves_at_the_half_life(self):
        self.assertAlmostEqual(recency_factor(ago(72), 3.0, NOW), 0.5, places=2)
        self.assertAlmostEqual(recency_factor(ago(0), 3.0, NOW), 1.0, places=2)
        self.assertGreater(recency_factor(ago(24), 30.0, NOW),
                           recency_factor(ago(24), 1.5, NOW))

    def test_beat_writer_on_an_injury_outranks_a_pundit_on_the_same_story(self):
        common = {"source_role": "REPORTING_UNDETERMINED", "published_at": ago(4),
                  "topics": ["INJURY"], "team_confidence": 0.95, "content_team_ids": [130]}
        beat = score_item({**common, "entity": BEAT_WRITER, "entity_team_ids": [130]}, now=NOW)
        pundit = score_item({**common, "entity": PUNDIT, "entity_team_ids": []}, now=NOW)
        self.assertGreater(beat["score"], pundit["score"])
        self.assertIn("source covers this team", beat["factors"])

    def test_community_reaction_cannot_outrank_reporting_on_role_alone(self):
        self.assertLess(ROLE_WEIGHT["COMMUNITY_REACTION"], ROLE_WEIGHT["REPORTING_UNDETERMINED"])
        self.assertLess(ROLE_WEIGHT["AGGREGATION"], ROLE_WEIGHT["OFFICIAL_CONFIRMATION"])

    def test_a_resolved_game_link_beats_an_unresolved_item(self):
        common = {"entity": BEAT_WRITER, "source_role": "REPORTING_UNDETERMINED",
                  "published_at": ago(4), "topics": ["GAME_PREVIEW"]}
        linked = score_item({**common, "game_score": 1.0}, now=NOW)
        loose = score_item({**common}, now=NOW)
        self.assertGreater(linked["score"], loose["score"])
        self.assertIn("linked to a scheduled game", linked["factors"])

    def test_every_score_explains_itself(self):
        result = score_item({"entity": BEAT_WRITER, "source_role": "ANALYSIS",
                             "published_at": ago(10), "topics": ["SCHEME_ANALYSIS"],
                             "team_confidence": 0.95}, now=NOW)
        self.assertTrue(result["factors"])
        self.assertTrue(any("expertise" in factor for factor in result["factors"]))
        self.assertLessEqual(result["score"], 100)

    def test_an_undated_item_is_penalised_rather_than_crashing(self):
        result = score_item({"entity": BEAT_WRITER, "source_role": "REPORTING_UNDETERMINED",
                             "published_at": None, "topics": ["INJURY"]}, now=NOW)
        self.assertGreater(result["score"], 0)
        self.assertIn("undated", result["factors"])


class RedditClassificationTests(unittest.TestCase):
    def test_crossposts_are_not_treated_as_outside_discovery(self):
        self.assertFalse(links_externally(
            {"url": "https://www.reddit.com/r/CFB/comments/abc/", "is_self": False}))
        self.assertFalse(links_externally({"url": "https://i.redd.it/x.png", "is_self": False}))
        self.assertTrue(links_externally(
            {"url": "https://www.freep.com/story/1", "is_self": False}))

    def test_structural_threads_are_recognised_before_generic_rules(self):
        self.assertEqual(reddit_content_type(
            {"title": "[Game Thread] Michigan at Ohio State", "is_self": False,
             "url": "https://www.reddit.com/x"}), "GAME_THREAD")
        self.assertEqual(reddit_content_type(
            {"title": "Postgame Thread: Oregon wins", "is_self": True}), "POSTGAME_THREAD")

    def test_link_submissions_become_discovery_not_reporting(self):
        self.assertEqual(reddit_content_type(
            {"title": "LT misses practice", "is_self": False,
             "url": "https://www.freep.com/story/1"}), "LINK_DISCOVERY")

    def test_community_defaults_reflect_the_subreddit(self):
        self.assertEqual(reddit_content_type(
            {"title": "Yards per play by quarter", "is_self": True}, "ANALYTICS"), "ANALYSIS")
        self.assertEqual(reddit_content_type(
            {"title": "My board", "is_self": True}, "DRAFT"), "SCOUTING_OPINION")


class MatchupRankingTests(unittest.TestCase):
    @staticmethod
    def signal(label, attack, defend, players=6, usage=800.0):
        return MatchupSignal(
            label=label, attack_team="Oregon", attack_label="Pass grade",
            attack_grade=attack, attack_players=players, attack_usage=usage,
            defend_team="Boise State", defend_label="Coverage",
            defend_grade=defend, defend_players=players, defend_usage=usage)

    def test_two_strong_units_rank_as_strength_on_strength(self):
        result = score_matchup(self.signal("Passing", 84.0, 82.0))
        self.assertEqual(result["archetype"], "STRENGTH_VS_STRENGTH")
        self.assertIsNone(result["advantage"])

    def test_a_clear_gap_names_the_favored_unit(self):
        result = score_matchup(self.signal("Passing", 85.0, 62.0))
        self.assertEqual(result["archetype"], "MISMATCH")
        self.assertEqual(result["advantage"], "Oregon")
        self.assertIn("Oregon", result["headline"])

    def test_two_poor_units_are_not_sold_as_watchable(self):
        weak = score_matchup(self.signal("Passing", 62.0, 60.0))
        strong = score_matchup(self.signal("Passing", 86.0, 84.0))
        self.assertEqual(weak["archetype"], "LOW_QUALITY")
        self.assertLess(weak["interest"], strong["interest"])

    def test_thin_samples_are_discounted_against_an_equal_matchup(self):
        full = score_matchup(self.signal("Passing", 84.0, 82.0))
        thin = score_matchup(self.signal("Passing", 84.0, 82.0, players=1, usage=20.0))
        self.assertLess(thin["interest"], full["interest"])
        self.assertFalse(thin["confident"])
        self.assertIn("limited graded sample", thin["reasons"])

    def test_incomplete_comparisons_are_dropped_not_guessed(self):
        signal = MatchupSignal("Passing", "Oregon", "Pass grade", 84.0, 6, 800.0,
                               "Boise State", "Coverage", None, 0, 0.0)
        self.assertIsNone(score_matchup(signal))
        self.assertEqual(rank_matchups([signal]), [])

    def test_ranking_puts_the_most_watchable_matchup_first(self):
        ranked = rank_matchups([
            self.signal("Rushing", 63.0, 61.0),
            self.signal("Passing", 88.0, 65.0),
            self.signal("Receiving", 70.0, 69.0),
        ])
        self.assertEqual(ranked[0]["label"], "Passing")

    def test_report_summarises_the_pff_packet(self):
        report = game_matchup_report([{
            "label": "Passing",
            "away_attacks": {"attack_label": "Pass grade",
                             "attack": {"grade": 85.0, "players": 4, "usage": 500},
                             "counter_label": "Coverage",
                             "counter": {"grade": 63.0, "players": 8, "usage": 900}},
            "home_attacks": {"attack_label": "Pass grade", "attack": None,
                             "counter_label": "Coverage", "counter": None},
        }], "Oregon", "Boise State")
        self.assertEqual(len(report["matchups"]), 1)
        self.assertEqual(report["mismatch_count"], 1)
        self.assertGreater(report["top_interest"], 0)


class RelevancePersistenceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = ContentRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_reddit_submission_credits_the_external_publisher(self):
        endpoint = {"endpoint_id": None, "source_entity_id": None,
                    "community_type": "GENERAL_CFB", "handle": "CFB"}
        content_id = self.repository.store_reddit_submission(endpoint, {
            "id": "t3_abc", "subreddit": "CFB", "title": "Starting tackle misses practice",
            "selftext": "", "url": "https://www.freep.com/story/1",
            "permalink": "https://www.reddit.com/r/CFB/comments/abc/",
            "domain": "freep.com", "is_self": False, "link_flair_text": "",
            "created_utc": NOW.timestamp(), "author": "someone",
        }, 2026)
        self.assertIsNotNone(content_id)
        import sqlite3
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute(
            "SELECT * FROM content_items WHERE content_id=?", (content_id,)).fetchone())
        self.assertEqual(row["publisher_name"], "freep.com")
        self.assertEqual(row["original_url"], "https://www.freep.com/story/1")
        self.assertEqual(row["content_type"], "LINK_DISCOVERY")
        # Reddit is the discovery surface, never the reporter.
        self.assertEqual(row["source_role"], "AGGREGATION")
        link_types = {inner[0] for inner in connection.execute(
            "SELECT link_type FROM content_links WHERE content_id=?", (content_id,))}
        self.assertEqual(link_types, {"ORIGINAL", "DISCOVERY"})
        connection.close()

    def test_rescore_persists_a_score_and_its_factors(self):
        endpoint = {"endpoint_id": None, "source_entity_id": None,
                    "community_type": "ANALYTICS", "handle": "CFBAnalysis"}
        self.repository.store_reddit_submission(endpoint, {
            "id": "t3_def", "subreddit": "CFBAnalysis", "title": "Success rate by down",
            "selftext": "chart", "url": "https://www.reddit.com/r/CFBAnalysis/comments/def/",
            "permalink": "https://www.reddit.com/r/CFBAnalysis/comments/def/",
            "domain": "self.CFBAnalysis", "is_self": True, "link_flair_text": "Analysis",
            "created_utc": NOW.timestamp(), "author": "someone",
        }, 2026)
        self.assertEqual(self.repository.rescore()["scored"], 1)
        import sqlite3
        connection = sqlite3.connect(self.path)
        row = connection.execute(
            "SELECT score,factors_json FROM content_relevance").fetchone()
        connection.close()
        self.assertGreater(row[0], 0)
        self.assertIn("role", row[1])
