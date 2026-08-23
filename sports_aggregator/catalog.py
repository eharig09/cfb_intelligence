"""Declarative league catalog.

Adding a league starts here. Web routes and API discovery read this catalog, while
provider implementations remain independent of Flask.
"""

from __future__ import annotations

from types import MappingProxyType

from sports_aggregator.models import FeedConfig, LeagueConfig


_LEAGUES = MappingProxyType(
    {
        "college-football": LeagueConfig(
            slug="college-football",
            name="College Football",
            sport="Football",
            abbreviation="CFB",
            description=(
                "National college-football headlines through a normalized, "
                "provider-independent feed. Team and conference views can build on this base."
            ),
            accent_color="#9a3412",
            feeds=(
                FeedConfig(
                    name="ESPN",
                    url="https://www.espn.com/espn/rss/ncf/news",
                    max_articles=40,
                    source_type="national_reporting",
                    reliability=4,
                    source_entity_key="organization:espn",
                    source_endpoint_key="rss:https://www.espn.com/espn/rss/ncf/news",
                ),
            ),
        ),
    }
)


def list_leagues() -> tuple[LeagueConfig, ...]:
    return tuple(_LEAGUES.values())


def get_league(slug: str) -> LeagueConfig | None:
    return _LEAGUES.get(slug)
