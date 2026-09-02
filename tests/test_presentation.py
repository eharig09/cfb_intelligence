import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from app import create_app
from sports_aggregator.cfb.draft import DRAFT_POSITION_ABBREVIATIONS, position_abbreviation
from sports_aggregator.cfb.identity import (
    CONFERENCE_COLORS, conference_color, contrast_ratio, foreground_for,
    readable_accent, team_identity,
)
from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.content import (
    ContentRepository, display_text, display_timestamp, linked_piece_metadata,
)


class DisplayTextTests(unittest.TestCase):
    """Video descriptions must never be rendered as a headline."""

    def test_a_title_always_wins_over_the_description(self):
        self.assertEqual(
            display_text({"title": "Ohio State 2026 preview",
                          "body_text": "SUBSCRIBE https://example.com #cfb #cfb"}),
            "Ohio State 2026 preview")

    def test_promotional_boilerplate_is_stripped_from_a_bodyless_item(self):
        result = display_text({"title": "",
                               "body_text": "Michigan LT missed practice. SUBSCRIBE at https://x.com"})
        self.assertEqual(result, "Michigan LT missed practice.")

    def test_urls_and_hashtag_runs_do_not_survive(self):
        result = display_text({"title": "", "body_text": "#a #b #c Real words here https://x.com/y"})
        self.assertNotIn("http", result)
        self.assertNotIn("#a", result)
        self.assertIn("Real words", result)

    def test_long_headlines_are_trimmed_with_an_ellipsis(self):
        result = display_text({"title": "x" * 400}, limit=50)
        self.assertEqual(len(result), 50)
        self.assertTrue(result.endswith("…"))

    def test_an_empty_item_is_labeled_rather_than_blank(self):
        self.assertEqual(display_text({"title": "", "body_text": ""}), "Untitled item")

    def test_timestamps_render_as_relative_labels(self):
        self.assertEqual(display_timestamp(None), "undated")
        self.assertNotIn("T", display_timestamp("2026-08-22T17:15:22+00:00"))

    def test_link_metadata_shows_source_exact_time_age_and_sound(self):
        metadata = linked_piece_metadata({
            "platform": "podcast", "source_name": "Saturday Show",
            "published_at": "2026-08-24T17:15:00+00:00",
        }, now=datetime(2026, 8, 24, 19, 15, tzinfo=timezone.utc),
           timezone_name="America/New_York")
        self.assertEqual(metadata["source_icon"], "🎧")
        self.assertEqual(metadata["source_type_label"], "Podcast")
        self.assertTrue(metadata["makes_sound"])
        self.assertEqual(metadata["published_exact"], "Aug 24, 2026 · 1:15 PM EDT")
        self.assertEqual(metadata["published_relative"], "2h ago")
        self.assertEqual(metadata["source_display_name"], "Saturday Show")


class IdentityTests(unittest.TestCase):
    """Team colors are chosen for helmets, so they need checking before use."""

    def test_a_white_team_color_is_darkened_until_it_is_visible(self):
        accent = readable_accent("#ffffff")
        self.assertIsNotNone(accent)
        self.assertGreaterEqual(
            contrast_ratio((int(accent[1:3], 16), int(accent[3:5], 16),
                            int(accent[5:7], 16)), (242, 245, 249)), 3.0)

    def test_a_pale_gold_keeps_its_hue_rather_than_becoming_grey(self):
        accent = readable_accent("#FFCB05")
        # Red channel stays dominant: the hue survives the darkening.
        self.assertGreater(int(accent[1:3], 16), int(accent[5:7], 16))

    def test_a_dark_color_is_left_alone(self):
        self.assertEqual(readable_accent("#00274C"), "#00274c")

    def test_foreground_flips_to_dark_text_on_a_light_fill(self):
        self.assertEqual(foreground_for("#FFCB05"), "#0b1728")
        self.assertEqual(foreground_for("#00274C"), "#ffffff")

    def test_missing_colors_do_not_raise(self):
        self.assertIsNone(readable_accent(None))
        self.assertIsNone(readable_accent("not-a-color"))
        self.assertEqual(foreground_for(None), "#ffffff")

    def test_every_conference_color_is_readable_on_the_page(self):
        for name, value in CONFERENCE_COLORS.items():
            rgb = (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
            self.assertGreaterEqual(contrast_ratio(rgb, (255, 255, 255)), 4.0, name)

    def test_an_unknown_conference_gets_a_neutral_color(self):
        self.assertEqual(conference_color("Some New League"), conference_color(None))

    def test_identity_bundles_everything_a_template_needs(self):
        identity = team_identity({"school": "Michigan", "color": "#00274c",
                                  "conference": "Big Ten"})
        self.assertEqual(set(identity) >= {"accent", "fill", "on_fill", "conference_color"}, True)


class PositionAbbreviationTests(unittest.TestCase):
    def test_long_position_names_have_a_short_form(self):
        self.assertEqual(position_abbreviation("Linebacker"), "LB")
        self.assertEqual(position_abbreviation("Defensive Edge"), "EDGE")
        self.assertEqual(position_abbreviation("Quarterback"), "QB")

    def test_unknown_positions_pass_through_unchanged(self):
        self.assertEqual(position_abbreviation("Wildcat"), "Wildcat")
        self.assertEqual(position_abbreviation(None), "—")

    def test_no_abbreviation_is_long_enough_to_wrap(self):
        for name, short in DRAFT_POSITION_ABBREVIATIONS.items():
            self.assertLessEqual(len(short), 4, name)


class ContextSeparationTests(unittest.TestCase):
    """Conference and team context must never read as reporting about the subject."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 68, "school": "Boise State", "mascot": "Broncos", "abbreviation": "BSU",
             "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
             "color": "#0033A0", "logos": []},
            {"id": 21, "school": "San Diego State", "mascot": "Aztecs", "abbreviation": "SDSU",
             "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
             "color": "#A6192E", "logos": []},
        )])
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Test", "Player", "Boise State", "QB", 1, 74, 200, 3),
        ))
        ContentRepository(self.path).initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
        })

    def tearDown(self):
        os.unlink(self.path)

    def test_team_page_separates_its_own_reporting_from_the_conference_wire(self):
        body = self.app.test_client().get("/college-football/teams/68/").get_data(as_text=True)
        self.assertIn("Team reporting", body)
        self.assertIn("Around the Mountain West", body)
        # The wire section has to say whose stories these are, or the heading
        # alone leaves a reader to assume they concern this team. The wording
        # shortened; what it has to establish did not.
        self.assertIn("Other programmes in the conference", body)

    def test_player_page_labels_team_reporting_as_context(self):
        body = self.app.test_client().get("/college-football/players/p1/").get_data(as_text=True)
        self.assertIn("Reporting on Test Player", body)
        self.assertIn("do not mention Test Player", body)

    def test_pages_carry_contrast_checked_identity_variables(self):
        body = self.app.test_client().get("/college-football/teams/68/").get_data(as_text=True)
        # Identity is sent as a light/dark pair; the stylesheet derives the
        # active --team from it, because an inline --team could not be
        # overridden by a theme rule.
        self.assertRegex(body, r"--team-light:#[0-9a-f]{6}")
        self.assertRegex(body, r"--team-dark:#[0-9a-f]{6}")
        self.assertRegex(body, r"--conference-light:#[0-9a-f]{6}")
        self.assertRegex(body, r"--conference-dark:#[0-9a-f]{6}")
        # A single hash only; the double-hash bug produced invalid CSS.
        self.assertNotIn("--team-light:##", body)
        self.assertNotIn("--team-dark:##", body)


class ResponsiveTests(unittest.TestCase):
    """The stylesheet must be usable on a phone without horizontal scrolling."""

    def setUp(self):
        with open("static/cfb.css", encoding="utf-8") as handle:
            self.css = handle.read()

    def test_layout_is_mobile_first(self):
        # Single-column defaults, with structure added at min-width breakpoints.
        self.assertIn("grid-template-columns: minmax(0, 1fr)", self.css)
        self.assertIn("@media (min-width: 860px)", self.css)

    def test_wide_content_scrolls_inside_its_own_container(self):
        self.assertIn("overflow-x: auto", self.css)

    def test_character_level_wrapping_is_scoped_away_from_tables(self):
        # A global `overflow-wrap: anywhere` split names like "Arch Manning".
        start = self.css.index("body {")
        body_rule = self.css[start:self.css.index("}", start)]
        self.assertNotIn("overflow-wrap", body_rule)
        self.assertIn("word-break: keep-all", self.css)

    def test_touch_targets_have_a_minimum_height(self):
        self.assertIn("min-height: 34px", self.css)

    def test_phone_navigation_and_tabs_do_not_squeeze_content(self):
        self.assertIn(".site-header .nav { flex-wrap: wrap", self.css)
        self.assertIn(".tabs { display: grid; grid-template-columns: repeat(2", self.css)
        self.assertIn(".table-wrap.mobile-compact", self.css)
        self.assertIn("overflow-x: auto", self.css)
        self.assertIn("width: max-content", self.css)

    def test_matchup_comparison_columns_have_team_group_rails(self):
        self.assertIn(".col-key-away-offense", self.css)
        self.assertIn("var(--away-team", self.css)
        self.assertIn(".col-key-home-offense", self.css)
        self.assertIn("var(--home-team", self.css)
        self.assertIn(".col-key-edge", self.css)

    def test_national_theme_uses_cool_broadcast_surfaces(self):
        """Charcoal and blue, not sepia. The values moved dark; the hue did not."""
        self.assertIn("--ink: #e6ecf3", self.css)
        self.assertIn("--cream: #10151c", self.css)
        self.assertIn("--accent: #6ea8f0", self.css)
        self.assertIn("--display-font:", self.css)

    def test_comparison_edges_avoid_cell_fills_and_use_a_centered_scale(self):
        start = self.css.index("table.data td.advantage {")
        rule = self.css[start:self.css.index("}", start)]
        self.assertNotIn("background:", rule)
        self.assertNotIn("box-shadow:", rule)
        self.assertIn("table.data td.advantage::before", self.css)
        self.assertIn(".comparison-scale", self.css)
        self.assertIn("left: 50%", self.css)


class PlayerMatchupTests(unittest.TestCase):
    """Individual matchups pair opposing positions and weight draft standing."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 1, "school": "Alpha", "mascot": "Ones", "abbreviation": "AL",
             "alternateNames": [], "conference": "SEC", "classification": "fbs",
             "color": "#123456", "logos": []},
            {"id": 2, "school": "Beta", "mascot": "Twos", "abbreviation": "BE",
             "alternateNames": [], "conference": "SEC", "classification": "fbs",
             "color": "#654321", "logos": []},
        )])
        self.repository.replace_players(2026, (
            Player("wr1", 2026, "Wide", "Out", "Alpha", "WR", 1, 73, 190, 3),
            Player("cb1", 2026, "Cover", "Man", "Beta", "CB", 2, 71, 185, 3),
            Player("s1", 2026, "Safe", "Zone", "Beta", "S", 3, 72, 195, 3),
        ))
        # The real pff_players schema is created by the repository; insert by
        # name so this fixture does not depend on its column order.
        connection = sqlite3.connect(self.path)
        connection.executemany(
            """INSERT INTO pff_players(season,pff_player_id,player_name,normalized_name,
               position,pff_team_name,cfbd_team_id,cfbd_team,cfbd_player_id,
               match_status,match_confidence,interest_score,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'CONFIRMED',1.0,?,'now')""", [
                (2025, "p1", "Wide Out", "wide out", "WR", "Alpha", 1, "Alpha", "wr1", 88.0),
                (2025, "p2", "Cover Man", "cover man", "CB", "Beta", 2, "Beta", "cb1", 86.0),
                (2025, "p3", "Safe Zone", "safe zone", "S", "Beta", 2, "Beta", "s1", 78.0),
            ])
        connection.executemany(
            """INSERT INTO pff_player_metrics VALUES(2025,?,?,'fixture',12,?,?,?,'now')""", [
                ("p1", "receiving", 88.0, 220, '{"targets":"80","yards":"1100"}'),
                ("p2", "coverage", 86.0, 240, '{"targets":"45","yards":"400"}'),
                ("p3", "coverage", 78.0, 220, '{"targets":"35","yards":"350"}'),
            ])
        connection.commit()
        connection.close()

    def tearDown(self):
        os.unlink(self.path)

    def _matchups(self, **kwargs):
        from sports_aggregator.cfb.player_matchups import player_matchups
        return player_matchups(self.repository, 2, 1, **kwargs)

    def test_opposing_positions_are_paired(self):
        matchups = self._matchups()
        self.assertEqual(len(matchups), 1)
        self.assertEqual(matchups[0]["label"], "Receiver vs coverage unit")
        self.assertEqual(matchups[0]["attacker"]["player_name"], "Wide Out")
        self.assertTrue(matchups[0]["defender"]["is_unit"])
        self.assertEqual(len(matchups[0]["defender"]["members"]), 2)

    def test_receiver_pairs_with_corner_only_for_heavy_man_sample(self):
        connection = sqlite3.connect(self.path)
        connection.execute(
            """INSERT INTO pff_supplemental_metrics VALUES(
               2025,'p2','Cover Man','CB','Beta','coverage_scheme',
               'REGULAR_SEASON_DETAIL','fixture',12,86,200,?,'now')""",
            ('{"base_snap_counts_coverage":"200","man_snap_counts_coverage":"130",'
             '"man_snap_counts_coverage_percent":"65"}',))
        connection.execute(
            """INSERT INTO pff_supplemental_metrics VALUES(
               2025,'p1','Wide Out','WR','Alpha','receiving_scheme',
               'REGULAR_SEASON_DETAIL','fixture',12,90,210,?,'now')""",
            ('{"man_grades_pass_route":"92","man_routes":"140",'
             '"zone_grades_pass_route":"81","zone_routes":"70"}',))
        connection.commit()
        connection.close()
        matchup = self._matchups()[0]
        self.assertEqual(matchup["label"], "Receiver vs heavy-man corner")
        self.assertEqual(matchup["defender"]["player_name"], "Cover Man")
        self.assertFalse(matchup["defender"].get("is_unit", False))
        self.assertEqual(matchup["attacker"]["interest_score"], 92.0)
        self.assertNotIn("matchup_metric", matchup["attacker"])

    def test_a_weakly_graded_side_removes_the_pairing(self):
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE pff_player_metrics SET primary_grade=50 WHERE pff_player_id='p1' AND dataset='receiving'")
        connection.commit()
        connection.close()
        self.assertEqual(self._matchups(), [])

    def test_players_no_longer_on_a_roster_cannot_be_matched(self):
        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE pff_players SET cfbd_player_id=NULL WHERE cfbd_player_id='wr1'")
        connection.commit()
        connection.close()
        self.assertEqual(self._matchups(), [])

    def test_board_rank_raises_a_pairing_without_creating_one(self):
        baseline = self._matchups()[0]["interest"]
        from sports_aggregator.cfb.prospects import initialize as initialize_prospects
        initialize_prospects(self.repository)
        connection = sqlite3.connect(self.path)
        connection.execute(
            """INSERT INTO draft_prospect_rankings(draft_year,source,rank,player_name,
               normalized_name,school,position,cfbd_player_id,cfbd_team_id,
               link_status,link_evidence,source_file,imported_at)
               VALUES(2027,'t',4,'Wide Out','wide out','Alpha','WR','wr1',1,
               'CONFIRMED','','f','now')""")
        connection.commit()
        connection.close()
        raised = self._matchups()[0]
        self.assertGreater(raised["interest"], baseline)
        self.assertEqual(raised["attacker"]["board_rank"], 4)
        self.assertTrue(any("2027 board" in reason for reason in raised["reasons"]))


class LinkAuditTests(unittest.TestCase):
    """Resolution rules are only trustworthy if they can be reviewed."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = ContentRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_audit_returns_a_shape_the_page_can_render(self):
        audit = self.repository.link_audit(kind="player", limit=5)
        self.assertEqual(audit["kind"], "player")
        self.assertEqual(audit["links"], [])
        self.assertIsInstance(audit["counts"], dict)

    def test_team_audit_is_available_too(self):
        self.assertEqual(self.repository.link_audit(kind="team", limit=5)["kind"], "team")


class UnscopedGuardTests(unittest.TestCase):
    """The widest matching rule needs the tightest guard."""

    def test_pro_football_context_blocks_an_unscoped_match(self):
        from sports_aggregator.social.context import allows_unscoped_match
        self.assertFalse(allows_unscoped_match(
            "How Chiefs star Trey Smith became an NFL success story",
            has_resolved_team=False))
        self.assertFalse(allows_unscoped_match(
            "3rd and short for the Jags backups", has_resolved_team=True))

    def test_college_context_permits_it(self):
        from sports_aggregator.social.context import allows_unscoped_match
        self.assertTrue(allows_unscoped_match(
            "SEC quarterback rankings and transfer portal news", has_resolved_team=False))

    def test_a_resolved_team_permits_it_without_explicit_vocabulary(self):
        from sports_aggregator.social.context import allows_unscoped_match
        self.assertTrue(allows_unscoped_match("Practice notes from Tuesday",
                                              has_resolved_team=True))

    def test_a_coaching_title_beside_a_name_marks_it_as_staff(self):
        from sports_aggregator.social.context import names_staff
        self.assertTrue(names_staff(
            "SEC previews: LSU coordinator Blake Baker returns", "Blake Baker"))
        self.assertFalse(names_staff(
            "Blake Baker caught two passes on Saturday", "Blake Baker"))


class PersonNameTests(unittest.TestCase):
    """Rosters and articles disagree about punctuation in initials."""

    def test_initials_collapse_to_one_token(self):
        from sports_aggregator.cfb.models import normalize_person_name
        self.assertEqual(normalize_person_name("C.J. Carr"), "cj carr")
        self.assertEqual(normalize_person_name("CJ Carr"), "cj carr")
        self.assertEqual(normalize_person_name("J.T. Daniels"), "jt daniels")

    def test_ordinary_names_are_unchanged(self):
        from sports_aggregator.cfb.models import normalize_person_name
        self.assertEqual(normalize_person_name("Arch Manning"), "arch manning")
        self.assertEqual(normalize_person_name("Amari Cooper"), "amari cooper")

    def test_suffixes_survive_normalization(self):
        from sports_aggregator.cfb.models import normalize_person_name
        self.assertEqual(normalize_person_name("A.J. Green Jr."), "aj green jr")

    def test_team_aliases_keep_the_original_rule(self):
        # Merging letters in team aliases would rewrite established entries.
        from sports_aggregator.cfb.models import normalize_alias
        self.assertEqual(normalize_alias("C.J. Carr"), "c j carr")


class StatCoverageTests(unittest.TestCase):
    """A silent conference gap left Notre Dame with no statistics for years."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 1, "school": "Alpha", "mascot": "Ones", "abbreviation": "AL",
             "alternateNames": [], "conference": "SEC", "classification": "fbs",
             "color": "#123456", "logos": []},
            {"id": 2, "school": "Solo", "mascot": "Ones", "abbreviation": "SO",
             "alternateNames": [], "conference": "FBS Independents",
             "classification": "fbs", "color": "#654321", "logos": []},
        )])
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "a1", "player": "A One", "team": "Alpha",
             "conference": "SEC", "position": "QB", "category": "passing",
             "statType": "YDS", "stat": "3000"},
        ], "SEC")

    def tearDown(self):
        os.unlink(self.path)

    def test_a_conference_with_no_rows_is_reported_as_a_gap(self):
        report = self.repository.stat_coverage()
        self.assertEqual(report["gap_count"], 1)
        self.assertEqual(report["gaps"][0]["conference"], "FBS Independents")
        self.assertEqual(report["gaps"][0]["season"], 2025)

    def test_a_complete_season_reports_no_gaps(self):
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "s1", "player": "S One", "team": "Solo",
             "conference": "FBS Independents", "position": "QB",
             "category": "passing", "statType": "YDS", "stat": "2000"},
        ], "FBS Independents")
        self.assertEqual(self.repository.stat_coverage()["gap_count"], 0)

    def test_explicit_seasons_can_be_requested(self):
        report = self.repository.stat_coverage(seasons=[2019, 2025])
        self.assertEqual(report["seasons"], [2019, 2025])
        # A season with nothing stored is all gaps, not an absent row.
        self.assertTrue(any(row["season"] == 2019 and row["total"] == 0
                            for row in report["grid"]))


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TeamPageLayoutTests(unittest.TestCase):
    """Two surfaces near the top of the team page were saying the same thing.

    The facts strip and the Team quality table both carried roster continuity
    and transfer arrivals, identically, a couple of hundred pixels apart. The
    table is the one with a Source column, so it keeps them.
    """

    def test_the_strip_and_the_quality_table_do_not_overlap(self):
        from sports_aggregator.cfb.views import quality_cards_table

        with open(os.path.join(PROJECT_ROOT, "templates", "cfb_team.html"),
                  encoding="utf-8") as handle:
            markup = handle.read()
        strip = markup[markup.index('<div class="facts">'):
                       markup.index('<nav class="mobile-page-tabs"')]
        for signal in ("Roster continuity", "Transfer arrivals"):
            with self.subTest(signal=signal):
                self.assertNotIn(signal, strip,
                                 "the Team quality table already says this, with a source")

    def test_the_strip_still_says_what_shape_the_roster_is(self):
        with open(os.path.join(PROJECT_ROOT, "templates", "cfb_team.html"),
                  encoding="utf-8") as handle:
            markup = handle.read()
        strip = markup[markup.index('<div class="facts">'):
                       markup.index('<nav class="mobile-page-tabs"')]
        self.assertIn("Roster size", strip)
        self.assertIn("Upperclassmen", strip)
        # Six cells: the grid is repeat(6) at full width and a short row would
        # leave holes in it.
        self.assertEqual(strip.count('<div class="fact">'), 6)

    def test_the_aside_table_headers_are_narrow_enough_for_the_aside(self):
        """A header wider than its numbers sets the column width."""
        from sports_aggregator.cfb.views import position_philosophy_table

        table = position_philosophy_table([], 2026)
        for column in table.columns:
            with self.subTest(column=column.key):
                self.assertLessEqual(len(column.label), 10, column.label)
