from __future__ import annotations

import os
import sqlite3
from contextlib import closing
import tempfile
import unittest

from app import create_app


class ContentAuditTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE teams (
                    team_id INTEGER PRIMARY KEY,
                    school TEXT NOT NULL
                );
                CREATE TABLE content_items (
                    content_id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT,
                    original_url TEXT,
                    publisher_name TEXT,
                    author_name TEXT,
                    published_at TEXT,
                    ingested_at TEXT,
                    content_type TEXT,
                    source_role TEXT
                );
                CREATE TABLE content_teams (
                    content_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    method TEXT NOT NULL,
                    PRIMARY KEY(content_id, team_id)
                );
                INSERT INTO teams(team_id, school) VALUES (1, 'North Texas');
                INSERT INTO content_items(
                    content_id, platform, title, canonical_url, publisher_name,
                    published_at, ingested_at, content_type, source_role
                ) VALUES (
                    10, 'rss', 'Regional Denton business story', 'https://example.com/story',
                    'Denton Daily', '2026-08-27T20:00:00+00:00',
                    '2026-08-27T20:05:00+00:00', 'REPORTING', 'REPORTING'
                );
                INSERT INTO content_teams(content_id, team_id, confidence, method)
                VALUES (10, 1, 0.71, 'alias_match');
                """
            )
        self.app = create_app({
            "TESTING": True,
            "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_DATABASE_PATH": self.path,
            "CFB_REFRESH_TOKEN": "audit-token",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    def _post(self, payload, token="audit-token"):
        return self.client.post(
            "/college-football/data-status/team-link-feedback",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_audit_page_shows_linkage_evidence(self):
        response = self.client.get("/college-football/data-status/")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("Regional Denton business story", text)
        self.assertIn("North Texas", text)
        self.assertIn("alias_match", text)

    def test_bad_link_is_removed_blocked_and_undoable(self):
        response = self._post({
            "content_id": 10, "team_id": 1, "action": "bad",
            "reason": "Regional story; not about North Texas football",
        })
        self.assertEqual(response.status_code, 200)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM content_teams WHERE content_id=10 AND team_id=1"
            ).fetchone())
            feedback = connection.execute(
                "SELECT verdict, previous_confidence, previous_method FROM content_team_feedback WHERE content_id=10 AND team_id=1"
            ).fetchone()
            self.assertEqual(feedback, ("bad", 0.71, "alias_match"))
            # Simulate a future automatic retag attempt. The moderation trigger must suppress it.
            connection.execute(
                "INSERT OR IGNORE INTO content_teams(content_id,team_id,confidence,method) VALUES (10,1,0.9,'retag')"
            )
            connection.commit()
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM content_teams WHERE content_id=10 AND team_id=1"
            ).fetchone())

        response = self._post({"content_id": 10, "team_id": 1, "action": "undo"})
        self.assertEqual(response.status_code, 200)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            restored = connection.execute(
                "SELECT confidence, method FROM content_teams WHERE content_id=10 AND team_id=1"
            ).fetchone()
            self.assertEqual(restored, (0.71, "alias_match"))
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM content_team_feedback WHERE content_id=10 AND team_id=1"
            ).fetchone())

    def test_moderation_requires_private_token(self):
        response = self._post({"content_id": 10, "team_id": 1, "action": "bad"}, token="wrong")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
