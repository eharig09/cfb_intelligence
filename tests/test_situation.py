"""What the Situation panel is allowed to call availability reporting.

The topic filter alone put four items on a TCU-North Carolina page, and three
of them were wrong: the same story twice, a story about a quarterback who had
left North Carolina and was starting at Wake Forest, and a Big 12 power
rankings video. Only the fourth said anything about who would be available.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from contextlib import closing

from app import create_app
from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.repository import CFBRepository, forget_initialized_schemas
from sports_aggregator.cfb.situations import (
    AVAILABILITY_MAX_TEAMS, AVAILABILITY_TYPES, availability_reports,
)
from sports_aggregator.social.content import ContentRepository


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AvailabilityFilterTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        ContentRepository(self.path).initialize()
        self.repository.replace_teams(tuple(
            Team(team_id, school, "M", "M", "ACC", None, "fbs", "000000",
                 "ffffff", (), (school,), None, None)
            for team_id, school in ((1, "North Carolina"), (2, "TCU"),
                                    (3, "Wake Forest"), (4, "BYU"), (5, "Utah"))))
        self.game = {"home_team_id": 2, "away_team_id": 1}

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _item(self, content_id, title, *, topics=("INJURY",),
              teams=((1, 0.95),), content_type="REPORTING"):
        with closing(self.repository._connect()) as connection:
            connection.execute(
                """INSERT INTO content_items(content_id,platform,platform_content_id,
                   canonical_url,original_url,title,body_text,summary,author_name,
                   publisher_name,published_at,ingested_at,content_type,source_role,
                   raw_json) VALUES(?,'rss',?,?,?,?,'B','S','A','Pub',
                   datetime('now'),datetime('now'),?,'REPORTING','{}')""",
                (content_id, str(content_id), "https://x.test/%d" % content_id,
                 "https://x.test/%d" % content_id, title, content_type))
            connection.execute(
                """INSERT INTO content_sport_decisions(content_id,sport,decision,
                   eligible,confidence,method,evidence_json,classifier_version,
                   decided_at) VALUES(?,'CFB','ACCEPT',1,1.0,'test','{}','v1',
                   datetime('now'))""", (content_id,))
            for topic in topics:
                connection.execute(
                    "INSERT INTO content_topics(content_id,topic,confidence,method)"
                    " VALUES(?,?,0.9,'test')", (content_id, topic))
            for team_id, confidence in teams:
                connection.execute(
                    "INSERT INTO content_teams(content_id,team_id,confidence,method)"
                    " VALUES(?,?,?,'test')", (content_id, team_id, confidence))
            connection.commit()

    def _titles(self):
        return [row["title"] for row in availability_reports(self.repository, self.game)]

    def test_a_plain_injury_report_is_kept(self):
        self._item(1, "Belichick looking to add to UNC's roster")
        self.assertEqual(self._titles(), ["Belichick looking to add to UNC's roster"])

    def test_an_item_tagged_twice_is_listed_once(self):
        """The duplicate visible on the page: one story, two topics."""
        self._item(1, "Two topics one story", topics=("DEPTH_CHART", "ROSTER"))
        self.assertEqual(self._titles(), ["Two topics one story"])

    def test_a_story_belonging_to_another_school_is_dropped(self):
        """Wake Forest 0.98, North Carolina 0.90: it is Wake Forest's news."""
        self._item(1, "Former UNC QB named starter at Wake Forest",
                   topics=("DEPTH_CHART",), teams=((3, 0.98), (1, 0.90)))
        self.assertEqual(self._titles(), [])

    def test_an_item_naming_half_a_conference_is_dropped(self):
        self._item(1, "BIG 12 POWER RANKINGS ARE HERE", topics=("ROSTER",),
                   teams=((1, 0.80), (2, 0.80), (3, 0.80), (4, 0.78), (5, 0.73)))
        self.assertEqual(self._titles(), [])

    def test_the_team_cap_is_the_thing_that_drops_it(self):
        self._item(1, "Three teams is still about a team", topics=("ROSTER",),
                   teams=((1, 0.95), (3, 0.80), (4, 0.78)))
        self.assertEqual(len(self._titles()), 1)
        self.assertLessEqual(3, AVAILABILITY_MAX_TEAMS)

    def test_a_ranking_or_a_preview_is_not_availability_reporting(self):
        for content_type in ("RANKINGS", "GAME_PREVIEW", "VIDEO_ANALYSIS",
                             "COMMUNITY_REACTION", "SOCIAL_POST"):
            with self.subTest(content_type=content_type):
                self.assertNotIn(content_type, AVAILABILITY_TYPES)

    def test_an_item_of_an_excluded_type_is_dropped(self):
        self._item(1, "Fall camp podcast", content_type="VIDEO_ANALYSIS")
        self.assertEqual(self._titles(), [])


class NavigationTests(unittest.TestCase):
    """Two things a reader does constantly, that were quietly broken."""

    def _markup(self, name):
        with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as handle:
            return handle.read()

    def test_every_year_switch_points_at_an_anchor_that_exists(self):
        """They pointed at ids the mobile tab script creates at runtime, so on
        a desktop the fragment matched nothing and the page jumped to the top."""
        for name in ("cfb_team.html", "cfb_game.html"):
            markup = self._markup(name)
            targets = set(re.findall(r'href="[^"]*#(tab-[a-z]+)"', markup))
            anchors = re.findall(r'id="(tab-[a-z]+)"', markup)
            with self.subTest(page=name):
                self.assertTrue(targets, "no year switch found")
                for target in targets:
                    self.assertIn(target, anchors)
                for anchor in anchors:
                    self.assertEqual(anchors.count(anchor), 1, "duplicate id")

    def test_the_team_page_eyebrow_goes_home(self):
        markup = self._markup("cfb_team.html")
        eyebrow = markup[markup.index('<div class="eyebrow">'):]
        self.assertIn("cfb.today", eyebrow[:eyebrow.index("</div>")])


class SituationLayoutTests(unittest.TestCase):
    """Four cards were drawn the height of the tallest one."""

    def test_the_cards_do_not_stretch_to_the_row(self):
        with open(os.path.join(ROOT, "static", "cfb.css"), encoding="utf-8") as handle:
            css = handle.read()
        block = css[css.index(".situation {"):css.index(".situation-card {")]
        self.assertIn("align-items: start", block)


if __name__ == "__main__":
    unittest.main()
