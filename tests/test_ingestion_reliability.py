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


def test_a_throttled_news_shard_keeps_its_partial_work_and_advances(monkeypatch):
    """The fetch loop stops at its deadline, stores what arrived, and moves the
    cursor to where it stopped -- so the next shard is the next block, not a
    retry of the one that timed out."""
    import time
    from sports_aggregator.social import local_reporting_shard as shard

    tasks = [
        ({"team_id": i, "team": f"T{i}"}, {"domain": f"d{i}.test", "name": f"S{i}"},
         FeedConfig(name=f"S{i}", url=f"https://d{i}.test/rss"))
        for i in range(6)
    ]

    class _Repo:
        path = ":memory:"
        def store_article(self, *a, **k): return None
        def record_run(self, *a, **k): return None

    class _SlowProvider:
        def __init__(self, config):
            self._slow = config.url.endswith(("d3.test/rss", "d4.test/rss", "d5.test/rss"))
        def fetch(self):
            if self._slow:
                time.sleep(5)
            return []

    written = {}
    monkeypatch.setattr(shard, "_tasks", lambda *a, **k: tasks)
    monkeypatch.setattr(shard, "ContentRepository", lambda *a, **k: _Repo())
    monkeypatch.setattr(shard, "_state_path", lambda repo: "state")
    monkeypatch.setattr(shard, "_read_cursor", lambda state: 0)
    monkeypatch.setattr(shard, "_write_cursor", lambda state, nxt, total: written.update(next=nxt))
    monkeypatch.setattr(shard, "RSSNewsProvider", _SlowProvider)

    report = shard.run_shard(2026, shard_size=6, workers=4, limit=5, deadline=0.4)

    assert report["status"] == "success"          # a partial pass still made progress
    assert 0 < report["completed"] < 6
    assert report["abandoned"] >= 1
    assert written["next"] == report["completed"]  # cursor stopped where the work did
