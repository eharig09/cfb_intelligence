from datetime import datetime, timezone
import os
import sqlite3
import tempfile
import unittest

from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.providers.youtube import YouTubeDataClient, configured_api_key
from sports_aggregator.social.content import ContentRepository, video_content_type
from sports_aggregator.social.media import (
    MediaRegistry, name_agreement, score_channel, score_podcast,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def channel(title, subscribers=60_000, videos=400, description="College football show"):
    return {"channel_id": "UC123", "title": title, "handle": "@show",
            "description": description, "subscribers": subscribers,
            "videos": videos, "uploads_playlist": "UU123"}


class ApiKeyTests(unittest.TestCase):
    def test_either_key_name_is_accepted(self):
        original = {name: os.environ.pop(name, None)
                    for name in ("YOUTUBE_API_KEY", "YOUTUBE_API")}
        try:
            os.environ["YOUTUBE_API"] = "legacy-name"
            self.assertEqual(configured_api_key(), "legacy-name")
            os.environ["YOUTUBE_API_KEY"] = "canonical-name"
            self.assertEqual(configured_api_key(), "canonical-name")
            self.assertEqual(YouTubeDataClient().api_key, "canonical-name")
        finally:
            for name in ("YOUTUBE_API_KEY", "YOUTUBE_API"):
                os.environ.pop(name, None)
                if original.get(name) is not None:
                    os.environ[name] = original[name]


class ChannelValidationTests(unittest.TestCase):
    def test_descriptive_suffixes_still_count_as_the_same_show(self):
        self.assertGreaterEqual(
            name_agreement("Split Zone Duo", "Split Zone Duo College Football"), 0.9)
        self.assertGreaterEqual(
            name_agreement("Joel Klatt Show", "The Joel Klatt Show: A College Football Podcast"), 0.9)

    def test_a_different_show_does_not_agree(self):
        self.assertLess(name_agreement("Split Zone Duo", "Reboot Zone"), 0.5)
        self.assertLess(name_agreement("College Football Enquirer", "Yahoo! Sports"), 0.5)

    def test_a_tiny_impersonator_channel_is_blocked(self):
        result = score_channel("The Solid Verbal", channel("The Solid Verbal", 11, 4))
        self.assertFalse(result["promotable"])
        self.assertIn("under 5,000 subscribers", result["blockers"])
        self.assertIn("under 25 uploads", result["blockers"])

    def test_a_large_but_unrelated_channel_is_blocked_on_name(self):
        result = score_channel("Joel Klatt Show", channel("The Herd with Colin Cowherd", 1_400_000))
        self.assertFalse(result["promotable"])
        self.assertIn("channel title does not clearly match the candidate", result["blockers"])

    def test_the_real_show_passes_with_reasons_recorded(self):
        result = score_channel("Cover 3 Podcast", channel("Cover 3 Podcast", 69_100, 4378))
        self.assertTrue(result["promotable"])
        self.assertEqual(result["blockers"], [])
        self.assertTrue(any("subscribers" in reason for reason in result["reasons"]))

    def test_a_feed_with_almost_no_episodes_is_blocked(self):
        result = score_podcast("Cover 3 Podcast", {
            "name": "Cover 3 Podcast", "artist": "CBS", "feed_url": "https://example.com/f.xml",
            "genres": ["Sports"], "episode_count": 5})
        self.assertFalse(result["promotable"])
        self.assertIn("under 20 published episodes", result["blockers"])

    def test_promotion_is_refused_for_an_unvalidated_match(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            registry = MediaRegistry(path)
            registry.initialize()
            blocked = score_channel("Split Zone Duo", channel("Reboot Zone", 13, 2))
            with self.assertRaises(ValueError):
                registry.promote_channel({"candidate_id": 1, "name": "Split Zone Duo",
                                          "proposed_classes": "PODCAST",
                                          "proposed_entity_type": "SHOW"}, blocked)
        finally:
            os.unlink(path)


class VideoClassificationTests(unittest.TestCase):
    def test_titles_map_to_the_right_content_type(self):
        cases = {
            "Ohio State vs Texas preview and predictions": "GAME_PREVIEW",
            "Coach Day press conference": "PRESS_CONFERENCE",
            "Film Room: breaking down the Oregon front": "FILM_BREAKDOWN",
            "2027 NFL Draft mock": "DRAFT_ANALYSIS",
            "Week 1 reaction: what we learned": "GAME_REACTION",
            "Top 25 power rankings": "RANKINGS",
        }
        for title, expected in cases.items():
            self.assertEqual(video_content_type(title), expected, title)

    def test_an_unmatched_title_falls_back_to_analysis(self):
        self.assertEqual(video_content_type("Why Alabama is different"), "VIDEO_ANALYSIS")


class TeamResolutionTests(unittest.TestCase):
    """The linking rules that were silently dropping and over-crediting teams."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.cfb = CFBRepository(self.path)
        # Built through the real payload path so aliases (school, abbreviation,
        # mascot) are generated exactly as they are in production.
        self.cfb.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 30, "school": "USC", "mascot": "Trojans", "abbreviation": "USC",
             "alternateNames": ["Southern California"], "conference": "Big Ten",
             "classification": "fbs", "color": "#990000", "logos": []},
            {"id": 99, "school": "LSU", "mascot": "Tigers", "abbreviation": "LSU",
             "alternateNames": ["Louisiana State"], "conference": "SEC",
             "classification": "fbs", "color": "#461D7C", "logos": []},
            {"id": 130, "school": "Michigan", "mascot": "Wolverines", "abbreviation": "MICH",
             "alternateNames": [], "conference": "Big Ten",
             "classification": "fbs", "color": "#00274C", "logos": []},
            {"id": 194, "school": "Ohio State", "mascot": "Buckeyes", "abbreviation": "OSU",
             "alternateNames": [], "conference": "Big Ten",
             "classification": "fbs", "color": "#BB0000", "logos": []},
            {"id": 2, "school": "Auburn", "mascot": "Tigers", "abbreviation": "AUB",
             "alternateNames": [], "conference": "SEC",
             "classification": "fbs", "color": "#0C2340", "logos": []},
            {"id": 5, "school": "Clemson", "mascot": "Tigers", "abbreviation": "CLEM",
             "alternateNames": [], "conference": "ACC",
             "classification": "fbs", "color": "#F56600", "logos": []},
            {"id": 6, "school": "Indiana", "mascot": "Hoosiers", "abbreviation": "IU",
             "alternateNames": [], "conference": "Big Ten",
             "classification": "fbs", "color": "#990000", "logos": []},
            {"id": 7, "school": "Oregon", "mascot": "Ducks", "abbreviation": "ORE",
             "alternateNames": [], "conference": "Big Ten",
             "classification": "fbs", "color": "#154733", "logos": []},
        )])
        self.content = ContentRepository(self.path)
        self.content.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def resolve(self, text):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            return {team_id: (round(confidence, 2), method)
                    for team_id, confidence, method
                    in self.content._team_candidates(connection, text)}
        finally:
            connection.close()

    def test_three_letter_programs_now_resolve(self):
        # A four-character alias floor previously discarded USC and LSU outright.
        self.assertIn(30, self.resolve("USC's recruiting class is loaded"))
        self.assertIn(99, self.resolve("Lane Kiffin arrives at LSU"))

    def test_lowercase_letter_runs_do_not_produce_a_team(self):
        self.assertEqual(self.resolve("discusses the usual suspects"), {})

    def test_a_team_named_in_the_lead_outranks_one_buried_in_the_body(self):
        text = "Michigan opens fall camp. " + ("filler " * 60) + " Auburn also practiced."
        resolved = self.resolve(text)
        self.assertGreater(resolved[130][0], resolved[2][0])

    def test_a_roundup_naming_many_teams_is_demoted_to_a_passing_mention(self):
        text = ("Ranking every program: Michigan, Ohio State, Auburn, Clemson, "
                "Indiana, Oregon, USC and LSU all moved this week.")
        resolved = self.resolve(text)
        self.assertGreater(len(resolved), self.content.LIST_MENTION_THRESHOLD)
        self.assertTrue(all(method == "list_mention" for _, method in resolved.values()))
        self.assertTrue(all(confidence < 0.5 for confidence, _ in resolved.values()))

    def test_a_focused_story_keeps_full_confidence(self):
        resolved = self.resolve("Michigan and Ohio State renew the rivalry Saturday")
        self.assertEqual({130, 194}, set(resolved))
        self.assertTrue(all(confidence >= 0.75 for confidence, _ in resolved.values()))

    def test_list_mentions_do_not_generate_game_or_conference_links(self):
        text = ("Ranking every program: Michigan, Ohio State, Auburn, Clemson, "
                "Indiana, Oregon, USC and LSU all moved this week.")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,canonical_url,
                   title,body_text,summary,author_name,publisher_name,published_at,
                   ingested_at,content_type,source_role,raw_json)
                   VALUES('test','x','u',?,'','','','',?,?,'POST','ANALYSIS','{}')""",
                (text, NOW.isoformat(), NOW.isoformat()))
            content_id = connection.execute(
                "SELECT content_id FROM content_items WHERE platform_content_id='x'").fetchone()[0]
            self.content._link_entities(connection, content_id, text, None, 2026)
            connection.commit()
            games = connection.execute(
                "SELECT COUNT(*) FROM content_games WHERE content_id=?", (content_id,)).fetchone()[0]
            conferences = connection.execute(
                "SELECT COUNT(*) FROM content_conferences WHERE content_id=?",
                (content_id,)).fetchone()[0]
            self.assertEqual(games, 0)
            self.assertEqual(conferences, 0)
        finally:
            connection.close()


class SourceStreamTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = ContentRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_streams_are_grouped_by_source_type_not_by_vendor(self):
        keys = [key for key, _, _, _ in ContentRepository.STREAM_DEFINITIONS]
        self.assertEqual(keys, ["reporting", "articles", "video", "podcasts", "community"])
        streams = self.repository.source_streams()
        self.assertEqual([stream["key"] for stream in streams], keys)
        self.assertTrue(all(stream["items"] == [] for stream in streams))

    def test_a_stored_video_appears_in_the_video_stream(self):
        endpoint = {"endpoint_id": None, "source_entity_id": None, "platform_id": "UC1",
                    "name": "Split Zone Duo", "classes": set()}
        self.repository.store_youtube_video(endpoint, {
            "video_id": "vid1", "title": "Ohio State preview", "description": "",
            "published_at": NOW, "url": "https://www.youtube.com/watch?v=vid1",
            "duration": "PT30M"}, 2026)
        streams = {stream["key"]: stream for stream in self.repository.source_streams()}
        self.assertEqual(streams["video"]["total"], 1)
        self.assertEqual(streams["video"]["items"][0]["content_type"], "GAME_PREVIEW")

    def test_a_podcast_episode_without_a_date_is_skipped(self):
        endpoint = {"endpoint_id": None, "source_entity_id": None, "name": "Show", "classes": set()}
        self.assertIsNone(self.repository.store_podcast_episode(endpoint, {
            "episode_id": "e1", "title": "Untitled", "description": "",
            "published_at": None, "page_url": "", "audio_url": ""}, 2026))
