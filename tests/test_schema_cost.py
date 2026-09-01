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

    ROWS = {
        "rankings": ("INSERT INTO rankings"
                     "(season, season_type, week, poll, is_final, rank, school) "
                     "VALUES(?,?,?,?,?,?,?)",
                     lambda n: (2025, "regular", 1, "AP", 0, n, f"Team {n}")),
        "teams": ("INSERT INTO teams(team_id, school, logos_json, updated_at) "
                  "VALUES(?,?,?,?)",
                  lambda n: (n, f"Team {n}", "[]", "2025-01-01")),
    }

    def _fill(self, table):
        statement, row = self.ROWS[table]
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executemany(statement, [row(n) for n in range(600)])
            connection.commit()

    def _reinitialize(self):
        forget_initialized_schemas()
        CFBRepository(self.path).initialize()

    def _rows_for(self, table):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM sqlite_stat1 WHERE tbl=?", (table,)).fetchone()[0]

    def test_a_table_filled_after_the_first_analyze_still_gets_statistics(self):
        """The failure this is here to stop, in miniature.

        Statistics used to be written once and then skipped forever if
        `sqlite_stat1` held any row, so tables that were empty at that moment
        stayed unanalyzed however large they grew. `cfb_plays` and
        `cfb_play_metrics` reached 650,000 rows that way, and the box score's
        pace query drove from all of `cfb_play_metrics` to return 166 rows:
        8.3 seconds, against 4.6ms once the planner had statistics.
        """
        CFBRepository(self.path).initialize()

        # One table is filled and analyzed first, exactly as `games` and the
        # box-score tables were. This is what put rows in `sqlite_stat1`, and
        # rows in `sqlite_stat1` were the whole of the old skip condition.
        self._fill("teams")
        self._reinitialize()
        self.assertTrue(self._rows_for("teams"))

        # A second table fills later, as the play tables did. The old check saw
        # statistics already present and skipped, leaving this one unanalyzed
        # however large it grew.
        self.assertEqual(self._rows_for("rankings"), 0)
        self._fill("rankings")
        self._reinitialize()
        self.assertTrue(self._rows_for("rankings"),
                        "a table filled after the first ANALYZE still needs "
                        "statistics -- this is the box score's 8.3 seconds")

    def test_statistics_are_not_rewritten_once_every_filled_table_has_them(self):
        """The check has to settle, or it pays for ANALYZE on every startup.

        Empty tables are the trap: ANALYZE records nothing for them, so a test
        of "has statistics" that ignored row counts would find them missing
        forever.
        """
        repository = CFBRepository(self.path)
        repository.initialize()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                CFBRepository._tables_missing_statistics(connection), [])

    def _mtimes(self):
        paths = (self.path, self.path + "-wal")
        return tuple(os.stat(p).st_mtime_ns if os.path.exists(p) else 0 for p in paths)

    def test_optimize_writes_nothing_when_nothing_has_changed(self):
        """A refresh that never touched this database must not empty the cache.

        The rendered-page cache is keyed on the database's modification time,
        and `optimize` runs after every scheduled refresh -- including news
        passes, which write to a different file entirely. ANALYZE writes, so
        running it unconditionally would throw away every cached page on every
        such pass.
        """
        repository = CFBRepository(self.path)
        repository.initialize()
        self._fill("teams")
        repository.optimize()
        self.assertTrue(self._rows_for("teams"))

        before = self._mtimes()
        repository.optimize()
        self.assertEqual(self._mtimes(), before,
                         "a second optimize with no write between must not touch the file")

    def test_optimize_analyzes_again_once_the_data_has_moved(self):
        repository = CFBRepository(self.path)
        repository.initialize()
        self._fill("teams")
        repository.optimize()

        self.assertEqual(self._rows_for("rankings"), 0)
        self._fill("rankings")
        repository.optimize()
        self.assertTrue(self._rows_for("rankings"),
                        "a write since the last ANALYZE means statistics are due")

    def test_optimize_analyzes_without_depending_on_what_it_queried_first(self):
        """`PRAGMA optimize` only considers tables the current connection has
        already read, and this opens a connection just to call it -- so the
        nightly refresh was running a guaranteed no-op. A bounded ANALYZE does
        not care what the connection touched first.
        """
        repository = CFBRepository(self.path)
        repository.initialize()
        self._fill("teams")
        repository.optimize()
        self._fill("rankings")

        repository.optimize()
        self.assertTrue(self._rows_for("rankings"),
                        "optimize must record statistics for the grown table")

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


class BatchedMatchupRowTests(unittest.TestCase):
    """The weekly slate loads grade rows once, not twice per game.

    `pff_matchups` reads exactly two teams, which suits a matchup page and not
    the dashboard: the weekly slate called it once per game, so twenty games
    issued forty queries fetching two teams each.
    """

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        with self.repository.transaction() as connection:
            for team_id, name in ((1, "Michigan"), (2, "Ohio State")):
                connection.execute(
                    """INSERT INTO pff_players(season,pff_player_id,player_name,
                       normalized_name,position,pff_team_name,cfbd_team_id,cfbd_team,
                       cfbd_player_id,candidate_cfbd_player_id,match_status,
                       match_confidence,interest_score,updated_at)
                       VALUES(2025,?,?,?, 'ED',?,?,?,?,NULL,'exact_name_same_team',
                       1.0,70.0,'2026-01-01')""",
                    (f"p{team_id}", name + " Edge", name.lower() + " edge",
                     name, team_id, name, f"c{team_id}"))
                connection.execute(
                    """INSERT INTO pff_player_metrics(season,pff_player_id,dataset,
                       source_file,game_count,primary_grade,usage_count,metrics_json,
                       imported_at) VALUES(2025,?, 'defense','t.csv',12,70.0,500.0,
                       '{}','2026-01-01')""", (f"p{team_id}",))

    def tearDown(self):
        forget_initialized_schemas()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_prefetched_rows_produce_the_same_matchups(self):
        """The optimization must be invisible in the output."""
        direct = self.repository.pff_matchups(1, 2, 2025)
        prefetched = self.repository.pff_matchup_rows([1, 2], 2025)
        batched = self.repository.pff_matchups(1, 2, 2025, prefetched=prefetched)
        self.assertEqual(direct, batched)

    def test_the_whole_slate_is_fetched_in_one_pass(self):
        seen = {"n": 0}
        real_connect = sqlite3.connect

        def traced(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(
                lambda sql: seen.__setitem__(
                    "n", seen["n"] + (1 if "pff_players" in str(sql) else 0)))
            return connection

        sqlite3.connect = traced
        try:
            self.repository.pff_matchup_rows([1, 2], 2025)
        finally:
            sqlite3.connect = real_connect
        # One statement per metrics table, regardless of how many teams.
        self.assertLessEqual(seen["n"], 2)

    def test_a_team_missing_from_the_prefetch_behaves_as_no_data(self):
        prefetched = self.repository.pff_matchup_rows([1], 2025)
        self.assertEqual(
            self.repository.pff_matchups(999, 998, 2025, prefetched=prefetched), [])

    def test_empty_and_invalid_team_lists_are_handled(self):
        self.assertEqual(self.repository.pff_matchup_rows([], 2025), {})
        self.assertEqual(self.repository.pff_matchup_rows([None, 0], 2025), {})
