from contextlib import closing

from sports_aggregator.cfb.models import Player
from sports_aggregator.cfb.player_injuries import initialize, parse_injury_articles
from sports_aggregator.cfb.player_injury_display import _notes
from sports_aggregator.cfb.repository import CFBRepository


def test_extracts_explicit_injury_story():
    payload = {
        "articles": [{
            "headline": "Quarterback suffers torn ACL, out for season",
            "description": "The quarterback will have knee surgery after the injury.",
            "published": "2023-09-30T12:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/example/injury"}},
        }]
    }
    rows = parse_injury_articles(payload, fallback_season=2026)
    assert len(rows) == 1
    assert rows[0]["season"] == 2023
    assert rows[0]["body_part"] == "ACL"
    assert rows[0]["season_ending"] is True
    assert rows[0]["confidence"] == "confirmed"


def test_ignores_absence_without_injury_language():
    payload = {
        "articles": [{
            "headline": "Quarterback will not start Saturday",
            "description": "The backup will get the start this week.",
            "published": "2024-10-05T12:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/example/depth-chart"}},
        }]
    }
    assert parse_injury_articles(payload, fallback_season=2026) == []


def test_deduplicates_same_source_url():
    article = {
        "headline": "Receiver dealing with ankle injury",
        "published": "2025-08-20T12:00:00Z",
        "links": {"web": {"href": "https://www.espn.com/example/ankle"}},
    }
    rows = parse_injury_articles({"articles": [article, article]}, fallback_season=2026)
    assert len(rows) == 1


def test_does_not_assign_another_players_injury_to_named_player():
    payload = {
        "articles": [{
            "headline": "Michigan injury update",
            "description": (
                "Michigan will be without starting left guard Giovanni El-Hadi for several more "
                "weeks with an injury, but could see safety Rod Moore play his first game since "
                "the end of the 2023 season this coming Saturday at Nebraska."
            ),
            "published": "2025-09-15T12:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/example/el-hadi-moore"}},
        }]
    }
    rows = parse_injury_articles(
        payload,
        fallback_season=2026,
        required_player_name="Rod Moore",
    )
    assert rows == []


def test_requires_named_player_and_injury_in_same_clause():
    payload = {
        "articles": [{
            "headline": "Rod Moore returns after knee injury",
            "description": "Rod Moore missed the 2024 season after suffering a knee injury.",
            "published": "2025-09-15T12:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/example/rod-moore"}},
        }]
    }
    rows = parse_injury_articles(
        payload,
        fallback_season=2026,
        required_player_name="Rod Moore",
    )
    assert len(rows) == 1
    assert rows[0]["body_part"] == "knee"


def _seed_event(repository, player_id, season, **overrides):
    row = {
        "injury_label": "Knee", "body_part": "knee", "details": "",
        "season_ending": 0, "source_name": "ESPN",
        "source_url": f"https://example.com/{player_id}-{season}",
        "source_published_at": f"{season}-09-01T00:00:00Z", "confidence": "reported",
    }
    row.update(overrides)
    with closing(repository._connect()) as connection:
        connection.execute(
            """INSERT INTO player_injury_events(
                   player_id,season,espn_athlete_id,injury_label,body_part,details,
                   season_ending,source_name,source_url,source_published_at,confidence,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(player_id), int(season), None, row["injury_label"], row["body_part"],
             row["details"], row["season_ending"], row["source_name"], row["source_url"],
             row["source_published_at"], row["confidence"], "2026-01-01T00:00:00Z"),
        )
        connection.commit()


def test_career_note_renders_for_the_matching_stint_season(tmp_path):
    repository = CFBRepository(tmp_path / "cfb.sqlite3")
    repository.initialize()
    initialize(repository)
    for season in (2024, 2025):
        repository.replace_players(season, [
            Player("rb", season, "Rod", "Moore", "Michigan", "S", 9, 72.0, 195, 3),
        ])
    _seed_event(repository, "rb", 2024, season_ending=1, body_part="ACL", injury_label="ACL")

    note = str(_notes(repository, "rb", 2024, stint_count=2))
    assert "injury-career-note" in note
    assert "ACL" in note and "season-ending" in note
    assert 'style=' not in note, "spacing belongs in cfb.css, not an inline style"

    # A season with no event, and a one-stint player, both stay silent.
    assert str(_notes(repository, "rb", 2025, stint_count=2)) == ""
    assert str(_notes(repository, "rb", 2024, stint_count=1)) == ""
