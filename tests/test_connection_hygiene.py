"""Connections have to be closed, not merely committed.

`with sqlite3.connect(path) as connection:` runs the *transaction* context
manager: it commits or rolls back on exit and leaves the connection open. Three
places in the app used it as though it closed, so the data-status page leaked a
file handle on every render and the moderation endpoint leaked one on every
post. On Windows it showed up as tests that passed their assertions and then
failed to delete their own temporary database.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from app import create_app


SOURCE_ROOTS = ("sports_aggregator", "app.py")


def _python_files():
    for root in SOURCE_ROOTS:
        path = Path(root)
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def test_no_module_treats_the_transaction_manager_as_a_close():
    offenders = []
    pattern = re.compile(r"^\s*with\s+(?!closing\()"
                         r"(sqlite3\.connect\(|self\._connect\(\)|repository\._connect\(\))")
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.match(line):
                offenders.append(f"{path}:{number}: {line.strip()}")
    assert not offenders, (
        "these commit but never close:\n  " + "\n  ".join(offenders))


@pytest.fixture()
def database(tmp_path):
    path = tmp_path / "cfb.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE teams (team_id INTEGER PRIMARY KEY, school TEXT);
            CREATE TABLE content_items (
                content_id INTEGER PRIMARY KEY, platform TEXT, title TEXT,
                canonical_url TEXT, publisher_name TEXT, published_at TEXT,
                ingested_at TEXT, content_type TEXT, source_role TEXT);
            CREATE TABLE content_teams (
                content_id INTEGER, team_id INTEGER, confidence REAL, method TEXT,
                PRIMARY KEY(content_id, team_id));
            INSERT INTO teams VALUES (1, 'North Texas');
            INSERT INTO content_items(content_id, platform, title, canonical_url)
                VALUES (10, 'rss', 'A story', 'https://example.com/s');
            INSERT INTO content_teams VALUES (10, 1, 0.71, 'alias_match');
        """)
    return path


def _app(database):
    return create_app({
        "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
        "CFB_DATABASE_PATH": str(database), "CFB_REFRESH_TOKEN": "t",
    })


def test_rendering_the_status_page_leaves_no_handle_behind(database):
    """The audit query runs on every render of that page."""
    client = _app(database).test_client()
    for _ in range(3):
        assert client.get("/college-football/data-status/").status_code == 200

    # On Windows an open handle is what stops this; elsewhere it always passes,
    # which is why the static check above exists as well.
    os.unlink(database)
    assert not database.exists()


def test_the_moderation_post_leaves_no_handle_behind(database):
    client = _app(database).test_client()
    response = client.post(
        "/college-football/data-status/team-link-feedback",
        json={"content_id": 10, "team_id": 1, "action": "bad", "reason": "x"},
        headers={"Authorization": "Bearer t"})
    assert response.status_code == 200, response.get_data(as_text=True)[:200]

    os.unlink(database)
    assert not database.exists()
