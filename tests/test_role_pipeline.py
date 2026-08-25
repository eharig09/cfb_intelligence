"""The role determination pass, and the wiring that makes it actually run.

`redetermine_roles` was written, tested in isolation, and never called by
anything. Items kept the ingestion-time `REPORTING_UNDETERMINED` placeholder and
had no row in `content_roles`, so the page had no evidence to show and said
"no role signal" on everything. These tests hold the wiring, not just the
classifier: a correct classifier nothing invokes is indistinguishable from a
broken one.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime

from sports_aggregator.bootstrap import steps
from sports_aggregator.models import Article
from sports_aggregator.social import content_cli
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.relevance import ROLE_WEIGHT
from sports_aggregator.social.roles import ROLE_LABELS, determine_role


class WiringTests(unittest.TestCase):

    def test_the_role_step_runs_in_both_phases(self):
        for phase in ("initial", "refresh"):
            with self.subTest(phase=phase):
                names = [step.name for step in steps(2026) if phase in step.phases]
                self.assertIn("roles", names)

    def test_roles_are_determined_after_clustering_and_before_scoring(self):
        """Determination reads cluster position; relevance reads the role."""
        for phase in ("initial", "refresh"):
            with self.subTest(phase=phase):
                order = [step.name for step in steps(2026) if phase in step.phases]
                self.assertLess(order.index("cluster"), order.index("roles"))
                self.assertLess(order.index("roles"), order.index("score"))

    def test_the_command_is_reachable_from_the_cli(self):
        """The bootstrap step shells out to this command by name."""
        with self.assertRaises(SystemExit) as raised:
            content_cli.main(["roles", "--help"])
        # --help exits 0; an unknown command would exit 2.
        self.assertEqual(raised.exception.code, 0)


class WeightTests(unittest.TestCase):

    def test_every_role_the_classifier_can_emit_carries_a_weight(self):
        """A role with no weight silently scores 0.5."""
        missing = sorted(set(ROLE_LABELS) - set(ROLE_WEIGHT))
        self.assertEqual(missing, [])

    def test_determining_a_role_does_not_lower_the_score(self):
        """`REPORTING` replaces `REPORTING_UNDETERMINED` and means the same.

        `REPORTING` was absent from the weights, so it fell to the 0.5 default
        while the placeholder it replaced scored 0.85 — running the classifier
        demoted the item.
        """
        self.assertEqual(ROLE_WEIGHT["REPORTING"],
                         ROLE_WEIGHT["REPORTING_UNDETERMINED"])
        self.assertGreater(ROLE_WEIGHT["REPORTING"], ROLE_WEIGHT["CORROBORATION"])
        self.assertLess(ROLE_WEIGHT["REPORTING"], ROLE_WEIGHT["ORIGINAL_REPORT"])


class ClassifierTests(unittest.TestCase):

    @staticmethod
    def _verdict(text, classes=frozenset({"BEAT_REPORTER"})):
        return determine_role(
            text=text, content_type="ARTICLE", classes=set(classes),
            platform="rss", links_external=False,
            cluster_position=None, cluster_size=1)

    def test_the_placeholder_is_never_re_emitted(self):
        """The classifier exists to replace REPORTING_UNDETERMINED."""
        for text in ("Sources tell me the starter is back",
                     "A plain practice note with no markers",
                     "per @reporter the deal is done", ""):
            with self.subTest(text=text[:30]):
                self.assertNotEqual(self._verdict(text)["role"],
                                    "REPORTING_UNDETERMINED")

    def test_every_verdict_carries_at_least_one_reason(self):
        """An empty evidence list is how the page detects an unclassified item."""
        for text in ("Sources tell me the starter is back",
                     "A plain practice note with no markers", ""):
            with self.subTest(text=text[:30]):
                self.assertTrue(self._verdict(text)["evidence"])

    def test_plain_reporting_is_distinguished_from_original_reporting(self):
        self.assertEqual(
            self._verdict("Sources tell me the starter is back")["role"],
            "ORIGINAL_REPORT")
        self.assertEqual(
            self._verdict("A plain practice note with no markers")["role"],
            "REPORTING")


class RedeterminationTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = ContentRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def _store(self, title, publisher="Beat Outlet"):
        self.repository.store_article(Article(
            title=title, url=f"https://pub.test/{abs(hash(title))}",
            source=publisher, publisher=publisher,
            original_url=f"https://pub.test/{abs(hash(title))}",
            summary="", published_at=datetime.fromisoformat("2026-08-20T14:00:00+00:00"),
            source_entity_key="publisher:beat", source_endpoint_key="rss:beat"), 2026)

    def test_redetermination_clears_every_placeholder_and_records_evidence(self):
        for title in ("Sources tell me the starter is back",
                      "A plain practice note",
                      "per @reporter the deal is done"):
            self._store(title)
        with closing(self.repository._connect()) as connection:
            # Force the ingestion-time placeholder the pass has to clear.
            connection.execute(
                "UPDATE content_items SET source_role='REPORTING_UNDETERMINED'")
            connection.commit()

        self.repository.redetermine_roles()

        with closing(self.repository._connect()) as connection:
            left = connection.execute(
                "SELECT COUNT(*) FROM content_items WHERE source_role='REPORTING_UNDETERMINED'"
            ).fetchone()[0]
            items = connection.execute("SELECT COUNT(*) FROM content_items").fetchone()[0]
            roles = connection.execute("SELECT COUNT(*) FROM content_roles").fetchone()[0]
            blank = connection.execute(
                "SELECT COUNT(*) FROM content_roles WHERE evidence_json IN ('[]','')"
            ).fetchone()[0]
        self.assertEqual(left, 0, "placeholder survived redetermination")
        self.assertEqual(roles, items, "every item needs a role row to explain itself")
        self.assertEqual(blank, 0, "a verdict with no evidence explains nothing")

    def test_redetermination_covers_items_ingested_after_the_last_run(self):
        """The original defect: the pass ran once and never caught up."""
        self._store("First batch item")
        self.repository.redetermine_roles()
        self._store("Second batch item arriving later")

        with closing(self.repository._connect()) as connection:
            before = connection.execute("SELECT COUNT(*) FROM content_roles").fetchone()[0]
            total = connection.execute("SELECT COUNT(*) FROM content_items").fetchone()[0]
        self.assertLess(before, total, "setup no longer reproduces the gap")

        self.repository.redetermine_roles()
        with closing(self.repository._connect()) as connection:
            after = connection.execute("SELECT COUNT(*) FROM content_roles").fetchone()[0]
        self.assertEqual(after, total)

    def test_redetermination_is_idempotent(self):
        self._store("A plain practice note")
        first = self.repository.redetermine_roles()
        second = self.repository.redetermine_roles()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
