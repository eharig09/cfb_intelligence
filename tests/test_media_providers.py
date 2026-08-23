import unittest

from sports_aggregator.providers.podcast import PodcastRSSClient
from sports_aggregator.providers.youtube import YouTubeDataClient


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.payload = payload; self.content = content
    def raise_for_status(self): return None
    def json(self): return self.payload


class YouTubeSession:
    def get(self, url, **kwargs):
        path = url.rsplit("/", 1)[-1]; params = kwargs["params"]
        if path == "channels" and params["part"] == "snippet,contentDetails":
            return FakeResponse({"items": [{"id": "UC123", "snippet": {
                "title": "Example Show", "description": "Analysis"}}]})
        if path == "channels":
            return FakeResponse({"items": [{"contentDetails": {
                "relatedPlaylists": {"uploads": "UU123"}}}]})
        if path == "videos":
            return FakeResponse({"items": [{"id": "vid1", "contentDetails": {"duration": "PT12M"}}]})
        return FakeResponse({"items": [{
            "snippet": {"title": "Week one", "description": "Preview",
                        "publishedAt": "2026-08-23T12:00:00Z",
                        "thumbnails": {"high": {"url": "https://img.example/1.jpg"}}},
            "contentDetails": {"videoId": "vid1", "videoPublishedAt": "2026-08-23T12:00:00Z"},
        }]})


class PodcastSession:
    def get(self, _url, **_kwargs):
        return FakeResponse(content=b'''<?xml version="1.0"?><rss version="2.0"><channel>
        <title>Example Podcast</title><description>CFB analysis</description><item>
        <guid>episode-1</guid><title>Opening week</title><link>https://example.com/one</link>
        <pubDate>Sun, 23 Aug 2026 12:00:00 GMT</pubDate>
        <enclosure url="https://example.com/one.mp3" type="audio/mpeg" />
        </item></channel></rss>''')


class MediaProviderTests(unittest.TestCase):
    def test_youtube_resolves_stable_channel_and_video_ids(self):
        client = YouTubeDataClient(api_key="test", session=YouTubeSession())
        resolution = client.resolve_channel(endpoint_key="youtube:candidate", handle="@example")
        self.assertEqual(resolution.status, "verified")
        self.assertEqual(resolution.platform_id, "UC123")
        videos = client.uploads("UC123")
        self.assertEqual(videos[0].video_id, "vid1")
        self.assertEqual(videos[0].duration, "PT12M")

    def test_podcast_feed_validation_and_episode_identity(self):
        client = PodcastRSSClient(session=PodcastSession())
        resolution = client.resolve("https://example.com/feed", "podcast:example")
        self.assertEqual(resolution.status, "verified")
        episode = client.episodes("https://example.com/feed")[0]
        self.assertEqual(episode.episode_id, "episode-1")
        self.assertEqual(episode.audio_url, "https://example.com/one.mp3")


if __name__ == "__main__":
    unittest.main()
