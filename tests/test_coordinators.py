from __future__ import annotations

from sports_aggregator.cfb.coordinators import board_url, parse_board, store_rows
from sports_aggregator.cfb.repository import CFBRepository


SAMPLE = """
<html><body>
<table>
<tr><th>School</th><th>OC</th><th>Rating</th><th>Yrs</th></tr>
<tr><td><a>Michigan</a></td><td>Jason Beck</td><td>92.5</td><td>3</td></tr>
<tr><td><a>Ohio State</a></td><td>Brian Hartline*</td><td>89.0</td><td>2</td></tr>
</table>
</body></html>
"""


def test_board_url():
    assert board_url(2026, "offense").endswith("board=offense&year=2026")


def test_parse_board():
    rows = parse_board(SAMPLE, "offense")
    assert rows == [
        {
            "team": "Michigan",
            "side": "offense",
            "role": "OC",
            "coach_name": "Jason Beck",
            "rating": 92.5,
            "experience_years": 3,
        },
        {
            "team": "Ohio State",
            "side": "offense",
            "role": "OC",
            "coach_name": "Brian Hartline",
            "rating": 89.0,
            "experience_years": 2,
        },
    ]


def test_store_rows_resolves_team(tmp_path):
    repo = CFBRepository(str(tmp_path / "cfb.sqlite3"))
    repo.initialize()
    with repo._connect() as connection:
        connection.execute(
            """INSERT INTO teams (
                team_id,school,mascot,abbreviation,conference,division,classification,
                color,alternate_color,logos_json,venue_id,venue_name,updated_at
            ) VALUES (1,'Michigan','Wolverines','MICH','Big Ten',NULL,'fbs',NULL,NULL,'[]',NULL,NULL,'now')"""
        )
        connection.commit()

    report = store_rows(repo, 2026, "https://example.test", parse_board(SAMPLE, "offense"))
    assert report["stored"] == 1
    assert report["unresolved"] == ["Ohio State"]

    with repo._connect() as connection:
        row = connection.execute(
            "SELECT coach_name,role,source_url FROM coordinator_seasons WHERE team_id=1"
        ).fetchone()
    assert tuple(row) == ("Jason Beck", "OC", "https://example.test")
