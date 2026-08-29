from sports_aggregator.cfb.player_injuries import parse_injury_articles


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
