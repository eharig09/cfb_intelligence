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


#: The current site links a logo (no text) in the school cell and suffixes the
#: name with a conference: "Delaware CUSA". Both broke the old parser -- the
#: empty link text won, so every row was dropped.
SAMPLE_LOGO_LINKS = """
<table>
<tr><th>School</th><th>DC</th><th>Rating</th><th>Yrs</th></tr>
<tr><td><a href="/t/193"><img src="x.png"></a>Delaware CUSA</td><td>Manny Diaz</td><td>4.1</td><td>1</td></tr>
<tr><td><a href="/t/87"><img src="y.png"></a>Notre Dame Ind</td><td>Al Golden</td><td>4.6</td><td>3</td></tr>
</table>
"""


def test_board_url():
    assert board_url(2026, "offense").endswith("board=offense&year=2026")


def test_parse_board_recovers_the_school_when_the_link_is_a_bare_logo():
    rows = parse_board(SAMPLE_LOGO_LINKS, "defense")
    assert [(row["team"], row["coach_name"]) for row in rows] == [
        ("Delaware", "Manny Diaz"), ("Notre Dame", "Al Golden")]


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


def _repo_with_michigan(tmp_path):
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
    return repo


def _one_row(team, side, role, coach, source="Wikipedia"):
    return {"team": team, "side": side, "role": role, "coach_name": coach,
            "rating": None, "experience_years": None, "source_name": source}


def test_if_absent_fills_a_gap_but_never_overwrites(tmp_path):
    repo = _repo_with_michigan(tmp_path)
    store_rows(repo, 2026, "wiki", [_one_row("Michigan", "defense", "DC", "Wink Martindale")])

    report = store_rows(
        repo, 2026, "pr",
        [_one_row("Michigan", "offense", "OC", "Chip Lindsey", "Punt & Rally"),
         _one_row("Michigan", "defense", "DC", "Someone Else", "Punt & Rally")],
        if_absent=True)

    assert report["stored"] == 1, "only the missing offense side is written"
    with repo._connect() as connection:
        rows = dict(connection.execute(
            "SELECT side,coach_name FROM coordinator_seasons WHERE team_id=1").fetchall())
    assert rows == {"offense": "Chip Lindsey", "defense": "Wink Martindale"}


def test_a_row_can_carry_its_own_source_name(tmp_path):
    repo = _repo_with_michigan(tmp_path)
    store_rows(repo, 2026, "wiki",
               [_one_row("Michigan", "offense", "OC", "Sherrone Moore", "Wikipedia (head coach)")])
    with repo._connect() as connection:
        source = connection.execute(
            "SELECT source_name FROM coordinator_seasons WHERE team_id=1").fetchone()[0]
    assert source == "Wikipedia (head coach)"
