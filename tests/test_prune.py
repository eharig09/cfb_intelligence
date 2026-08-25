"""Space reporting and tiered pruning.

This tool deletes data, so the tests care most about what it leaves alone:
recent reporting, current seasons, and anything outside the tier being applied.
The default is a dry run, and that is asserted too — a prune that ran because
someone forgot a flag is the failure mode worth preventing.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from sports_aggregator.cfb import prune_cli
from sports_aggregator.cfb.models import Game, Team
from sports_aggregator.cfb.repository import (
    CFBRepository, forget_initialized_schemas,
)
from sports_aggregator.social.content import ContentRepository


def _game(game_id: int, season: int) -> Game:
    return Game.from_cfbd({
        "id": game_id, "season": season, "week": 1, "seasonType": "regular",
        "startDate": f"{season}-09-05T19:30:00.000Z", "startTimeTBD": False,
        "completed": True, "neutralSite": False, "conferenceGame": True,
        "venue": "Stadium", "venueId": 1,
        "homeId": 1, "homeTeam": "Michigan", "homeConference": "Big Ten",
        "homePoints": 30, "awayId": 2, "awayTeam": "Ohio State",
        "awayConference": "Big Ten", "awayPoints": 27,
    })


class PruneTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        self.repository.replace_teams((
            Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None, "fbs",
                 "00274C", "FFCB05", (), ("Michigan",), None, None),
            Team(2, "Ohio State", "Buckeyes", "OSU", "Big Ten", None, "fbs",
                 "BB0000", "666666", (), ("Ohio State",), None, None),
        ))
        self.repository.replace_games(2019, [_game(11, 2019)])
        self.repository.replace_games(2026, [_game(22, 2026)])
        ContentRepository(self.path).initialize()
        self._seed()

    def tearDown(self):
        forget_initialized_schemas()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _seed(self):
        with self.repository.transaction() as connection:
            for game_id, season in ((11, 2019), (22, 2026)):
                connection.execute(
                    """INSERT INTO game_player_box_stats(game_id,team_id,team,
                       player_id,player,category,stat_type,stat_value,numeric_value)
                       VALUES(?,1,'Michigan','p1','A Player','rushing','YDS','100',100)""",
                    (game_id,))
                connection.execute(
                    """INSERT INTO player_season_stats(season,player_id,player,team,
                       position,category,stat_type,stat_value,numeric_value)
                       VALUES(?,'p1','A Player','Michigan','RB','rushing','YDS','100',100)""",
                    (season,))
            for content_id, published, payload in (
                    (1, "2020-01-01T00:00:00+00:00", '{"big":"old payload"}'),
                    (2, "2099-01-01T00:00:00+00:00", '{"big":"fresh payload"}')):
                connection.execute(
                    """INSERT INTO content_items(content_id,platform,platform_content_id,
                       canonical_url,original_url,title,body_text,summary,author_name,
                       publisher_name,published_at,ingested_at,content_type,source_role,
                       raw_json) VALUES(?,'rss',?,?,?,'T','B','S','A','P',?,
                       '2026-01-01T00:00:00+00:00','ARTICLE','REPORTING',?)""",
                    (content_id, f"i{content_id}", f"https://x.test/{content_id}",
                     f"https://x.test/{content_id}", published, payload))

    def _count(self, sql, params=()):
        with closing(self.repository._connect()) as connection:
            return connection.execute(sql, params).fetchone()[0]

    # -- reporting ---------------------------------------------------------

    def test_the_report_names_what_each_tier_costs(self):
        entries = prune_cli.report(self.repository, raw_days=30,
                                   content_days=180, before_season=2021)
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertIn("loses", entry)
            self.assertGreaterEqual(entry["megabytes"], 0)
        self.assertIn("nothing", entries[0]["loses"])

    def test_reporting_alone_changes_nothing(self):
        before = self._count("SELECT COUNT(*) FROM content_items")
        prune_cli.report(self.repository, raw_days=0, content_days=0,
                         before_season=2030)
        self.assertEqual(self._count("SELECT COUNT(*) FROM content_items"), before)

    def test_the_command_defaults_to_a_dry_run(self):
        rows = self._count("SELECT COUNT(*) FROM game_player_box_stats")
        prune_cli.main(["--database", self.path, "--before-season", "2030",
                        "--content-days", "0", "--raw-days", "0"])
        self.assertEqual(self._count("SELECT COUNT(*) FROM game_player_box_stats"), rows)

    # -- raw payloads ------------------------------------------------------

    def test_blanking_payloads_keeps_the_article_itself(self):
        prune_cli.prune_raw_payloads(self.repository, older_than_days=30)
        self.assertEqual(self._count("SELECT COUNT(*) FROM content_items"), 2)
        self.assertEqual(
            self._count("SELECT raw_json FROM content_items WHERE content_id=1"), "{}")
        self.assertEqual(self._count("SELECT title FROM content_items WHERE content_id=1"), "T")

    def test_recent_payloads_are_left_alone(self):
        prune_cli.prune_raw_payloads(self.repository, older_than_days=30)
        self.assertIn("fresh",
                      self._count("SELECT raw_json FROM content_items WHERE content_id=2"))

    # -- archived reporting ------------------------------------------------

    def test_only_reporting_past_the_window_is_removed(self):
        removed = prune_cli.prune_archived_reporting(self.repository, older_than_days=180)
        self.assertEqual(removed, 1)
        self.assertEqual(self._count("SELECT COUNT(*) FROM content_items"), 1)
        self.assertEqual(
            self._count("SELECT content_id FROM content_items"), 2, "the fresh item")

    def test_pruning_reporting_does_not_touch_game_data(self):
        prune_cli.prune_archived_reporting(self.repository, older_than_days=180)
        self.assertEqual(self._count("SELECT COUNT(*) FROM game_player_box_stats"), 2)

    # -- archived seasons --------------------------------------------------

    def test_only_seasons_before_the_cutoff_are_dropped(self):
        removed = prune_cli.prune_archived_seasons(self.repository, before_season=2021)
        self.assertGreater(removed, 0)
        remaining = self._count(
            """SELECT COUNT(*) FROM game_player_box_stats b
               JOIN games g ON g.game_id=b.game_id WHERE g.season < 2021""")
        self.assertEqual(remaining, 0)
        kept = self._count(
            """SELECT COUNT(*) FROM game_player_box_stats b
               JOIN games g ON g.game_id=b.game_id WHERE g.season >= 2021""")
        self.assertEqual(kept, 1, "the current season must survive")

    def test_the_games_themselves_are_kept(self):
        """Schedules and results stay; only the per-play detail goes."""
        prune_cli.prune_archived_seasons(self.repository, before_season=2021)
        self.assertEqual(self._count("SELECT COUNT(*) FROM games WHERE season=2019"), 1)

    def test_season_stats_follow_the_same_cutoff(self):
        prune_cli.prune_archived_seasons(self.repository, before_season=2021)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM player_season_stats WHERE season<2021"), 0)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM player_season_stats WHERE season>=2021"), 1)

    def test_a_cutoff_before_all_data_removes_nothing(self):
        self.assertEqual(
            prune_cli.prune_archived_seasons(self.repository, before_season=1900), 0)

    def test_vacuum_leaves_a_working_database(self):
        prune_cli.prune_archived_seasons(self.repository, before_season=2021)
        prune_cli.vacuum(self.repository)
        self.assertEqual(self._count("SELECT COUNT(*) FROM teams"), 2)
        self.assertEqual(self._count("SELECT COUNT(*) FROM games"), 2)


if __name__ == "__main__":
    unittest.main()
