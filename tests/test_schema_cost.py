"""Schema setup and planner statistics.

Every repository method began with `self.initialize()`, which opened a
connection and replayed its whole CREATE-TABLE script. The social repositories
compounded it: ContentRepository.initialize() built a fresh CFBRepository and
ran the entire CFB schema, and StoryRepository built a ContentRepository on top
of that. Rendering one matchup page opened 137 connections and issued 4,431
statements, about 3,500 of them re-creating tables that already existed.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from sports_aggregator.cfb.repository import (
    CFBRepository, forget_initialized_schemas,
)
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.stories import StoryRepository


class StatementCountTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)          # a path, not yet a database
        forget_initialized_schemas()

    def tearDown(self):
        forget_initialized_schemas()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _count_statements(self, action) -> int:
        seen = {"n": 0}
        real_connect = sqlite3.connect

        def traced(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(
                lambda _sql: seen.__setitem__("n", seen["n"] + 1))
            return connection

        sqlite3.connect = traced
        try:
            action()
        finally:
            sqlite3.connect = real_connect
        return seen["n"]

    def test_the_schema_is_created_once_not_once_per_call(self):
        repository = CFBRepository(self.path)
        first = self._count_statements(repository.initialize)
        second = self._count_statements(repository.initialize)
        self.assertGreater(first, 50, "first call should build the schema")
        self.assertEqual(second, 0, "second call should do no database work")

    def test_a_second_instance_on_the_same_database_is_also_free(self):
        """The social repositories construct fresh instances internally."""
        CFBRepository(self.path).initialize()
        self.assertEqual(
            self._count_statements(CFBRepository(self.path).initialize), 0)

    def test_the_social_repositories_do_not_replay_the_cfb_schema(self):
        ContentRepository(self.path).initialize()
        again = self._count_statements(ContentRepository(self.path).initialize)
        self.assertEqual(again, 0)
        stories = self._count_statements(StoryRepository(self.path).initialize)
        self.assertGreater(stories, 0, "story schema still has to be created")
        self.assertEqual(
            self._count_statements(StoryRepository(self.path).initialize), 0)

    def test_a_database_removed_underneath_is_rebuilt(self):
        """The memo must not outlive the file it describes."""
        repository = CFBRepository(self.path)
        repository.initialize()
        os.unlink(self.path)
        rebuilt = self._count_statements(repository.initialize)
        self.assertGreater(rebuilt, 50, "a missing database must be recreated")
        self.assertTrue(os.path.exists(self.path))


class PlannerStatisticsTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()

    def tearDown(self):
        forget_initialized_schemas()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_statistics_are_generated_when_the_schema_is_created(self):
        """Without them SQLite guessed join order badly enough to scan.

        The continuity lookup drove from pff_player_metrics, reading every row
        for the season, instead of starting from the few pff_players rows for
        one team that idx_pff_players_team already indexes: 462ms against 16ms
        for the same five teams.
        """
        CFBRepository(self.path).initialize()
        with closing(sqlite3.connect(self.path)) as connection:
            present = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='sqlite_stat1'").fetchone()[0]
        self.assertTrue(present, "ANALYZE should have run at least once")

    def test_optimize_is_safe_to_call_and_keeps_statistics_current(self):
        repository = CFBRepository(self.path)
        repository.initialize()
        repository.optimize()   # must not raise
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertTrue(connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='sqlite_stat1'").fetchone()[0])

    def test_statistics_failures_never_break_initialization(self):
        """A database that refuses ANALYZE still has to answer queries."""
        repository = CFBRepository(self.path)
        original = CFBRepository.__dict__["_ensure_statistics"]

        def explode(connection):
            raise sqlite3.OperationalError("no")

        CFBRepository._ensure_statistics = staticmethod(explode)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                repository.initialize()
        finally:
            CFBRepository._ensure_statistics = original
        forget_initialized_schemas()
        repository.initialize()
        self.assertTrue(repository.get_team(1) is None)


if __name__ == "__main__":
    unittest.main()
