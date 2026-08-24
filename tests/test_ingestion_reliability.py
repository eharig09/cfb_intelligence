import json

from sports_aggregator.models import FeedConfig
from sports_aggregator.providers.rss import RSSNewsProvider
from sports_aggregator.social.bluesky import BlueskyIdentityClient


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _BlueskySession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        cursor = (params or {}).get("cursor")
        if not cursor:
            return _Response({
                "feed": [{"post": {"uri": f"at://post/{i}"}} for i in range(40)],
                "cursor": "page-2",
            })
        return _Response({
            "feed": [{"post": {"uri": f"at://post/{i}"}} for i in range(40, 60)]
        })


def test_bluesky_author_feed_includes_replies_and_recovers_at_least_50_posts():
    session = _BlueskySession()
    client = BlueskyIdentityClient(session=session)

    feed = client.author_feed("did:plc:test", limit=15)

    assert len(feed) == 50
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["filter"] == "posts_with_replies"
    assert session.calls[1]["params"]["cursor"] == "page-2"


def test_local_reporting_prefers_native_rss_and_keeps_google_as_fallback(tmp_path, monkeypatch):
    registry_dir = tmp_path / "data" / "local_sources"
    registry_dir.mkdir(parents=True)
    registry = {
        "teams": {
            "1": {
                "team_id": 1,
                "sources": [
                    {
                        "domain": "example.com",
                        "native_rss": "https://example.com/native.xml",
                        "sports_rss": None,
                        "google_news_rss": "https://news.google.com/rss/search?q=example",
                    }
                ],
            }
        }
    }
    (registry_dir / "cfb_local_source_registry.json").write_text(
        json.dumps(registry), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    requested = []

    def parser(url):
        requested.append(url)
        return {
            "entries": [
                {
                    "title": "Example football practice update",
                    "link": "https://example.com/story",
                    "summary": "Depth chart notes",
                }
            ]
        }

    config = FeedConfig(
        name="Example News",
        url="https://news.google.com/rss/search?q=example",
        source_type="local_reporting",
        source_entity_key="local-publisher:example-com",
        source_endpoint_key="rss:google-news:example-com:1",
    )

    articles = RSSNewsProvider(config, parser=parser).fetch()

    assert requested == ["https://example.com/native.xml"]
    assert len(articles) == 1
    assert articles[0].discovered_via == "RSS_NATIVE"


def test_local_reporting_falls_back_when_native_feed_fails(tmp_path, monkeypatch):
    registry_dir = tmp_path / "data" / "local_sources"
    registry_dir.mkdir(parents=True)
    registry = {
        "teams": {
            "1": {
                "team_id": 1,
                "sources": [
                    {
                        "domain": "example.com",
                        "native_rss": "https://example.com/native.xml",
                        "sports_rss": None,
                        "google_news_rss": "https://news.google.com/rss/search?q=example",
                    }
                ],
            }
        }
    }
    (registry_dir / "cfb_local_source_registry.json").write_text(
        json.dumps(registry), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    requested = []

    def parser(url):
        requested.append(url)
        if "example.com/native" in url:
            raise RuntimeError("native feed unavailable")
        return {
            "entries": [
                {
                    "title": "Example football update",
                    "link": "https://example.com/story",
                }
            ]
        }

    config = FeedConfig(
        name="Example News",
        url="https://news.google.com/rss/search?q=example",
        source_type="local_reporting",
        source_entity_key="local-publisher:example-com",
        source_endpoint_key="rss:google-news:example-com:1",
    )

    articles = RSSNewsProvider(config, parser=parser).fetch()

    assert requested == [
        "https://example.com/native.xml",
        "https://news.google.com/rss/search?q=example",
    ]
    assert len(articles) == 1
    assert articles[0].discovered_via == "RSS"
