import csv
import os
import tempfile
import unittest

from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.review import (
    ContentReviewRepository,
    binary_metrics,
    multilabel_metrics,
    spearman,
)


class ReviewMetricTests(unittest.TestCase):
    def test_binary_metrics(self):
        result = binary_metrics([(True, True), (True, False), (False, True), (False, False)])
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)

    def test_multilabel_metrics(self):
        result = multilabel_metrics([({"INJURY", "ROSTER"}, {"INJURY"}), (set(), set())])
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["exact_match"], 0.5)

    def test_spearman_tracks_rank_direction(self):
        self.assertEqual(spearman([(10, 1), (20, 2), (30, 3)]), 1.0)
        self.assertEqual(spearman([(10, 3), (20, 2), (30, 1)]), -1.0)


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        content = ContentRepository(self.path)
        content.initialize()
        content.store_reddit_submission({
            "endpoint_id": None, "source_entity_id": None,
            "community_type": "GENERAL_CFB", "handle": "CFB",
        }, {
            "id": "review-one", "subreddit": "CFB",
            "title": "College football playoff rankings reaction",
            "selftext": "", "url": "https://reddit.com/r/CFB/comments/review-one/",
            "permalink": "https://reddit.com/r/CFB/comments/review-one/",
            "domain": "self.CFB", "is_self": True, "link_flair_text": "",
            "created_utc": 1787486400, "author": "reader",
        }, 2026)
        content.rescore()
        self.reviews = ContentReviewRepository(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_csv_round_trip_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "review.csv")
            exported = self.reviews.export_csv(output, limit=10, reviewer="editor")
            self.assertEqual(exported["exported"], 1)
            with open(output, "r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["label_relevant"] = rows[0]["predicted_relevant"]
            rows[0]["label_topics"] = rows[0]["predicted_topics"] or "NONE"
            rows[0]["label_role"] = rows[0]["predicted_role"]
            rows[0]["label_team_ids"] = "NONE"
            rows[0]["label_player_keys"] = "NONE"
            rows[0]["label_priority"] = "3"
            with open(output, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            imported = self.reviews.import_csv(output, reviewer="editor")
            self.assertEqual(imported["saved"], 1)
            report = self.reviews.report(reviewer="editor")
            self.assertEqual(report["reviews"], 1)
            self.assertEqual(report["relevance"]["accuracy"], 1.0)
            self.assertEqual(report["topics"]["exact_match"], 1.0)
            self.assertEqual(report["roles"]["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
