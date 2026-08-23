import csv
import os
import tempfile
import unittest

from sports_aggregator.cfb.draft import (
    PFF_TO_DRAFT_POSITION, _percentile, calibration, position_targets, prospect_board,
)
from sports_aggregator.cfb.models import Team
from sports_aggregator.cfb.prospects import (
    BOARD_SCHOOL_ALIASES, board_position, import_board, read_board, reconcile, strip_suffix,
)
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.roles import determine_role, role_label


class RoleDeterminationTests(unittest.TestCase):
    """Roles must be determined from the item, not from the source class alone."""

    reporter = {"NATIONAL_REPORTER"}
    analyst = {"NATIONAL_ANALYST"}

    def test_first_hand_attribution_reads_as_original_reporting(self):
        verdict = determine_role(text="Sources tell me the starter will miss the opener.",
                                 content_type="SOCIAL_POST", classes=self.reporter,
                                 platform="bluesky")
        self.assertEqual(verdict["role"], "ORIGINAL_REPORT")
        self.assertIn("attributes to its own sources", verdict["evidence"])

    def test_crediting_another_outlet_outranks_a_first_hand_marker(self):
        # "per @x, sources say" is relaying someone else's work, not reporting.
        verdict = determine_role(text="Per @Someone, sources tell them the QB is out.",
                                 content_type="SOCIAL_POST", classes=self.reporter,
                                 platform="bluesky")
        self.assertEqual(verdict["role"], "CORROBORATION")

    def test_a_reporter_with_no_origin_marker_is_plain_reporting(self):
        verdict = determine_role(text="The team practiced indoors on Tuesday.",
                                 content_type="SOCIAL_POST", classes=self.reporter,
                                 platform="bluesky")
        self.assertEqual(verdict["role"], "REPORTING")
        self.assertIn("no origin marker in the text", verdict["evidence"])

    def test_a_later_cluster_item_reads_as_corroboration(self):
        verdict = determine_role(text="The team practiced indoors on Tuesday.",
                                 content_type="SOCIAL_POST", classes=self.reporter,
                                 platform="bluesky", cluster_position=3, cluster_size=4)
        self.assertEqual(verdict["role"], "CORROBORATION")
        self.assertIn("item 3 of 4 in its story cluster", verdict["evidence"])

    def test_official_channels_and_bots_short_circuit(self):
        self.assertEqual(determine_role(text="x", content_type=None,
                                        classes={"OFFICIAL_TEAM"}, platform="rss")["role"],
                         "OFFICIAL_CONFIRMATION")
        self.assertEqual(determine_role(text="x", content_type=None, classes={"BOT"},
                                        platform="bluesky")["role"], "AUTOMATED")

    def test_content_type_beats_text_markers(self):
        verdict = determine_role(text="Sources tell me everything",
                                 content_type="LINK_DISCOVERY", classes=self.reporter,
                                 platform="reddit")
        self.assertEqual(verdict["role"], "AGGREGATION")

    def test_opinion_from_an_analyst_is_analysis_not_opinion(self):
        self.assertEqual(determine_role(text="My take: they should fire the coordinator.",
                                        content_type=None, classes=self.analyst,
                                        platform="bluesky")["role"], "ANALYSIS")
        self.assertEqual(determine_role(text="My take: they should fire the coordinator.",
                                        content_type=None, classes=set(),
                                        platform="bluesky")["role"], "COMMENTARY")

    def test_every_verdict_carries_evidence(self):
        for classes in (self.reporter, self.analyst, set()):
            verdict = determine_role(text="Anything at all", content_type=None,
                                     classes=classes, platform="bluesky")
            self.assertTrue(verdict["evidence"])

    def test_labels_are_human_readable(self):
        self.assertEqual(role_label("REPORTING_UNDETERMINED"), "Reporting")
        self.assertEqual(role_label("ORIGINAL_REPORT"), "Original report")
        self.assertEqual(role_label(None), "Unclassified")


class BoardParsingTests(unittest.TestCase):
    def test_generational_suffixes_are_stripped_for_matching(self):
        self.assertEqual(strip_suffix("terrance carter jr"), "terrance carter")
        self.assertEqual(strip_suffix("damon wilson ii"), "damon wilson")
        # A two-word name is never reduced further, even if it looks like a suffix.
        self.assertEqual(strip_suffix("john v"), "john v")

    def test_board_positions_map_to_the_cfbd_vocabulary(self):
        self.assertEqual(board_position("EDGE"), "Defensive Edge")
        self.assertEqual(board_position("iol"), "Offensive Guard")
        self.assertEqual(board_position(None), "Unknown")

    def test_ambiguous_school_names_are_mapped_explicitly(self):
        self.assertEqual(BOARD_SCHOOL_ALIASES["mississippi"], "ole miss")

    def test_rows_without_a_usable_rank_are_skipped(self):
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        try:
            with open(path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Rank", "Player", "School", "Position"])
                writer.writerow(["1", "Real Player", "Texas", "QB"])
                writer.writerow(["", "No Rank", "Texas", "QB"])
                writer.writerow(["3", "", "Texas", "QB"])
            entries = read_board(path)
            self.assertEqual([entry["player_name"] for entry in entries], ["Real Player"])
        finally:
            os.unlink(path)


class DraftCalibrationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 1, "school": "Texas", "mascot": "Longhorns", "abbreviation": "TEX",
             "alternateNames": [], "conference": "SEC", "classification": "fbs",
             "color": "#BF5700", "logos": []},
            {"id": 2, "school": "Ole Miss", "mascot": "Rebels", "abbreviation": "MISS",
             "alternateNames": [], "conference": "SEC", "classification": "fbs",
             "color": "#CE1126", "logos": []},
        )])

    def tearDown(self):
        os.unlink(self.path)

    def test_percentile_places_a_value_in_its_distribution(self):
        self.assertEqual(_percentile([70.0, 75.0, 80.0, 85.0], 85.0), 1.0)
        self.assertEqual(_percentile([70.0, 75.0, 80.0, 85.0], 60.0), 0.0)
        self.assertEqual(_percentile([], 80.0), 0.0)

    def test_every_pff_position_maps_into_the_draft_vocabulary(self):
        for code, name in PFF_TO_DRAFT_POSITION.items():
            self.assertTrue(name and name[0].isupper(), code)

    def test_an_empty_store_yields_an_empty_board_without_raising(self):
        board = prospect_board(self.repository, roster_season=2026, limit=5)
        self.assertEqual(board["prospects"], [])
        self.assertEqual(board["eligible_pool"], 0)
        self.assertEqual(position_targets(board), [])
        self.assertEqual(calibration(self.repository)["matched_picks"], 0)

    def test_import_records_identity_status_for_every_row(self):
        from sports_aggregator.cfb.models import Player
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Arch", "Manning", "Texas", "QB", 16, 76, 225, 3),
            Player("p2", 2026, "Suntarine", "Perkins", "Ole Miss", "LB", 5, 75, 230, 3),
        ))
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        try:
            with open(path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Rank", "Player", "School", "Position"])
                writer.writerow(["1", "Arch Manning", "Texas", "QB"])
                # "Mississippi" only resolves through the explicit board alias map.
                writer.writerow(["2", "Suntarine Perkins", "Mississippi", "LB"])
                writer.writerow(["3", "Nobody Here", "Texas", "WR"])
            counts = import_board(self.repository, path, draft_year=2027,
                                  source="test", roster_season=2026)
            self.assertEqual(counts["rows"], 3)
            self.assertEqual(counts["confirmed"], 2)
            self.assertEqual(counts["unresolved"], 1)
        finally:
            os.unlink(path)

    def test_reconcile_separates_missing_evidence_from_disagreement(self):
        from sports_aggregator.cfb.models import Player
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Arch", "Manning", "Texas", "QB", 16, 76, 225, 3),
        ))
        handle, path = tempfile.mkstemp(suffix=".csv")
        os.close(handle)
        try:
            with open(path, "w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Rank", "Player", "School", "Position"])
                writer.writerow(["1", "Arch Manning", "Texas", "QB"])
            import_board(self.repository, path, draft_year=2027, source="test",
                         roster_season=2026)
            board = prospect_board(self.repository, roster_season=2026, limit=50)
            result = reconcile(self.repository, board, draft_year=2027)
            # With no PFF profile stored, the player is "no profile", never a
            # claim that the board is wrong about him.
            self.assertEqual(len(result["no_profile"]), 1)
            self.assertEqual(result["board_ahead"], [])
            self.assertEqual(result["agree"], [])
        finally:
            os.unlink(path)


class EloTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()

    def tearDown(self):
        os.unlink(self.path)

    def test_missing_elo_yields_an_empty_map_rather_than_zeros(self):
        self.assertEqual(self.repository.team_elo(2026), {})
