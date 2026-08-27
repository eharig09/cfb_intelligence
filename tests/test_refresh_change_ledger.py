import sqlite3

from sports_aggregator.tracked_refresh import _diff_table, _snapshot


def _make_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE game_lines (season INTEGER, game_id INTEGER, provider TEXT, spread REAL, total REAL, PRIMARY KEY(season, game_id, provider))"
        )
        connection.executemany(
            "INSERT INTO game_lines VALUES(?,?,?,?,?)",
            [
                (2026, 1, "DraftKings", -3.5, 51.5),
                (2026, 2, "DraftKings", 2.5, 47.0),
                (2025, 99, "DraftKings", -1.0, 44.0),
            ],
        )
        connection.commit()


def test_change_ledger_counts_added_changed_and_removed(tmp_path):
    database = tmp_path / "cfb.sqlite3"
    snapshot = tmp_path / "before.sqlite3"
    _make_database(database)

    copied = _snapshot(database, snapshot, 2026)
    assert "game_lines" in copied

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE game_lines SET spread=-4.5 WHERE season=2026 AND game_id=1 AND provider='DraftKings'"
        )
        connection.execute(
            "DELETE FROM game_lines WHERE season=2026 AND game_id=2 AND provider='DraftKings'"
        )
        connection.execute(
            "INSERT INTO game_lines VALUES(?,?,?,?,?)",
            (2026, 3, "DraftKings", -7.0, 55.0),
        )
        connection.commit()

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("ATTACH DATABASE ? AS snap", (str(snapshot),))
        result = _diff_table(connection, "game_lines")

    assert result is not None
    assert result["added"] == 1
    assert result["changed"] == 1
    assert result["removed"] == 1
    changed = next(sample for sample in result["samples"] if sample["kind"] == "changed")
    spread = next(field for field in changed["fields"] if field["field"] == "spread")
    assert spread["before"] == "-3.5"
    assert spread["after"] == "-4.5"
