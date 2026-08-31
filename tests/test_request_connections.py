import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from app import create_app
from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas


def team(team_id, school, mascot):
    return Team.from_cfbd({
        "id": team_id, "school": school, "mascot": mascot, "abbreviation": school[:3].upper(),
        "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
        "color": "#0033A0", "logos": []})


class CountingConnections:
    """Count how many real sqlite connections a block of work opens."""

    def __enter__(self):
        self.opened = 0
        self._real = sqlite3.connect

        def counting(*args, **kwargs):
            self.opened += 1
            return self._real(*args, **kwargs)

        sqlite3.connect = counting
        return self

    def __exit__(self, *exc):
        sqlite3.connect = self._real


class RequestConnectionTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([team(68, "Boise State", "Broncos"),
                                       team(21, "San Diego State", "Aztecs")])
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DATABASE_PATH": self.path,
            "CFB_DEFAULT_SEASON": 2026})

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_reads_in_one_request_share_a_connection(self):
        with self.app.test_request_context("/"):
            self.repository.initialize()
            with CountingConnections() as counter:
                for _ in range(5):
                    self.repository.latest_rankings(2026)
            self.assertEqual(counter.opened, 1)

    def test_the_connection_does_not_outlive_the_request(self):
        with self.app.test_request_context("/"):
            self.repository.latest_rankings(2026)
        # Windows refuses to remove a file while a handle is open on it, so a
        # successful unlink is proof the teardown ran. tearDown tolerates the
        # file already being gone.
        os.unlink(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_outside_a_request_every_read_still_closes_its_own_connection(self):
        """A CLI command or a test must not be left holding the database."""
        self.repository.initialize()
        with CountingConnections() as counter:
            for _ in range(3):
                self.repository.latest_rankings(2026)
        self.assertEqual(counter.opened, 3)

    def test_a_writer_never_borrows_the_reader(self):
        """A failed write must not roll back somebody else's read work."""
        with self.app.test_request_context("/"):
            self.repository.latest_rankings(2026)          # opens the reader
            with CountingConnections() as counter:
                with self.repository.transaction() as connection:
                    connection.execute("SELECT 1")
            self.assertEqual(counter.opened, 1)

    def test_a_failed_write_leaves_the_request_readable(self):
        with self.app.test_request_context("/"):
            self.repository.get_team(68)
            with self.assertRaises(sqlite3.OperationalError):
                with self.repository.transaction() as connection:
                    connection.execute("INSERT INTO no_such_table VALUES (1)")
            self.assertEqual(self.repository.get_team(68)["school"], "Boise State")


class TeamLookupTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([team(68, "Boise State", "Broncos")])
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DATABASE_PATH": self.path,
            "CFB_DEFAULT_SEASON": 2026})

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_repeated_lookups_read_the_table_once(self):
        with self.app.test_request_context("/"):
            self.repository.get_team(68)
            with CountingConnections() as counter:
                for _ in range(8):
                    self.repository.get_team(68)
            self.assertEqual(counter.opened, 0)

    def test_a_caller_cannot_corrupt_the_shared_row(self):
        with self.app.test_request_context("/"):
            first = self.repository.get_team(68)
            first["school"] = "Tampered"
            first["logos"].append("nonsense")
            second = self.repository.get_team(68)
            self.assertEqual(second["school"], "Boise State")
            self.assertEqual(second["logos"], [])

    def test_an_unknown_team_is_still_none(self):
        with self.app.test_request_context("/"):
            self.assertIsNone(self.repository.get_team(999999))

    def test_a_write_from_elsewhere_is_seen_by_the_next_request(self):
        """The memo lasts a request; the page cache depends on that."""
        with self.app.test_request_context("/"):
            self.assertEqual(self.repository.get_team(68)["mascot"], "Broncos")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE teams SET mascot='Broncos!' WHERE team_id=68")
            connection.commit()
        with self.app.test_request_context("/"):
            self.assertEqual(self.repository.get_team(68)["mascot"], "Broncos!")


if __name__ == "__main__":
    unittest.main()
