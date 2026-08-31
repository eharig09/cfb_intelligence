import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from sports_aggregator.cfb import play_by_play, expected_points_v2, team_game_advanced
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


class SchemaMemoTests(unittest.TestCase):
    """Creating a table that already exists is idempotent, not free.

    These initializers call each other -- `team_game_advanced` initializes
    `expected_points_v2`, which initializes `play_by_play` -- so one page render
    replayed the whole cascade a dozen times against a 900 MB database.
    """

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.addCleanup(forget_initialized_schemas)
        self.addCleanup(self._remove)

    def _remove(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def table_names(self, path):
        # Closed explicitly: sqlite3's context manager commits but does not
        # close, and an open handle keeps Windows from removing the file.
        with closing(sqlite3.connect(path)) as connection:
            return {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}

    def connections_during(self, call):
        opened = []
        real = sqlite3.connect

        def counting(*args, **kwargs):
            opened.append(args[0] if args else kwargs.get("database"))
            return real(*args, **kwargs)

        with mock.patch("sqlite3.connect", counting):
            call()
        return len(opened)

    def test_the_schema_is_still_created(self):
        play_by_play.initialize(self.repository)
        names = self.table_names(self.path)
        self.assertIn("cfb_plays", names)

    def test_a_repeat_call_opens_no_connection(self):
        play_by_play.initialize(self.repository)
        self.assertEqual(
            self.connections_during(lambda: play_by_play.initialize(self.repository)), 0)

    def test_the_cascade_settles_after_the_first_pass(self):
        team_game_advanced.initialize(self.repository)
        names = self.table_names(self.path)
        self.assertLessEqual({"cfb_plays", "cfb_expected_points_state",
                              "cfb_team_game_advanced"}, names)
        # The whole chain, ten times over, and not one connection.
        self.assertEqual(self.connections_during(
            lambda: [team_game_advanced.initialize(self.repository) for _ in range(10)]), 0)

    def test_a_second_database_is_initialized_on_its_own_merits(self):
        """The memo is keyed by path, so one database does not mask another."""
        play_by_play.initialize(self.repository)
        handle, other = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(other) and os.unlink(other))
        play_by_play.initialize(CFBRepository(other))
        names = self.table_names(other)
        self.assertIn("cfb_plays", names)

    def test_a_database_deleted_underneath_the_memo_is_rebuilt(self):
        """A test that rebuilds a database in place must not get an empty one."""
        play_by_play.initialize(self.repository)
        os.unlink(self.path)
        play_by_play.initialize(self.repository)
        names = self.table_names(self.path)
        self.assertIn("cfb_plays", names)

    def test_forgetting_the_memo_lets_every_module_run_again(self):
        expected_points_v2.initialize(self.repository)
        forget_initialized_schemas()
        self.assertGreater(self.connections_during(
            lambda: expected_points_v2.initialize(self.repository)), 0)


if __name__ == "__main__":
    unittest.main()
