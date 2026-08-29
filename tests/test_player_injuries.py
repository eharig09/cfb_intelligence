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
