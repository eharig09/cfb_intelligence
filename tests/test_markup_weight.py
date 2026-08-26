"""Markup the browser never reads is weight the browser still pays for.

A team page shipped 623 KB for 359 table rows — about 1.7 KB each — and a fifth
of it was attributes nothing consumed: `data-label` on every cell (read by no
stylesheet or script), `data-sort-value` repeating the text already in the cell
(the sort script falls back to `textContent`), and `col-key-` on every cell of
every table when the stylesheet targets eight keys on one page.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from app import create_app
from sports_aggregator.cfb.models import Game, Player, Team
from sports_aggregator.cfb.repository import (
    CFBRepository, forget_initialized_schemas,
)
from sports_aggregator.social.content import ContentRepository


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLESHEET = os.path.join(ROOT, "static", "cfb.css")
TABLE_MACROS = os.path.join(ROOT, "templates", "_tables.html")


class AttributeDisciplineTests(unittest.TestCase):

    def test_the_styled_column_allowlist_matches_the_stylesheet(self):
        """The allowlist exists to avoid emitting classes nothing selects.

        If a rule is added for a new column key and the allowlist is not
        updated, the class is never emitted and the rule silently does nothing.
        """
        with open(STYLESHEET, encoding="utf-8") as handle:
            css = handle.read()
        with open(TABLE_MACROS, encoding="utf-8") as handle:
            macros = handle.read()

        targeted = {name.removeprefix("col-key-")
                    for name in re.findall(r"\.(col-key-[a-z-]+)", css)}
        block = re.search(r"STYLED_COLUMN_KEYS = \(([^)]*)\)", macros, re.S)
        self.assertIsNotNone(block, "allowlist not found in the table macros")
        allowed = {value.replace("_", "-")
                   for value in re.findall(r"'([a-z_]+)'", block.group(1))}
        self.assertEqual(targeted - allowed, set(),
                         "the stylesheet targets a column key the macro never emits")

    def test_data_label_is_not_emitted(self):
        """Nothing reads it; it was 40 KB on one page."""
        with open(TABLE_MACROS, encoding="utf-8") as handle:
            self.assertNotIn("data-label", handle.read())

    def test_nothing_reads_data_label(self):
        """Guards the removal: if something starts using it, this fails."""
        for name in ("cfb.css", "cfb_tables.js"):
            with open(os.path.join(ROOT, "static", name), encoding="utf-8") as handle:
                self.assertNotIn("data-label", handle.read())


class RenderedWeightTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        forget_initialized_schemas()
        repository = CFBRepository(self.path)
        repository.replace_teams([Team.from_cfbd(payload) for payload in (
            {"id": 68, "school": "Boise State", "mascot": "Broncos",
             "abbreviation": "BSU", "alternateNames": [],
             "conference": "Mountain West", "classification": "fbs",
             "color": "#0033A0", "logos": []},
            {"id": 21, "school": "San Diego State", "mascot": "Aztecs",
             "abbreviation": "SDSU", "alternateNames": [],
             "conference": "Mountain West", "classification": "fbs",
             "color": "#A6192E", "logos": []},
        )])
        repository.replace_games(2026, [Game.from_cfbd({
            "id": 401, "season": 2026, "week": 3, "seasonType": "regular",
            "startDate": "2026-09-12T19:30:00.000Z", "startTimeTBD": False,
            "completed": False, "neutralSite": False, "conferenceGame": True,
            "venue": "Snapdragon Stadium", "venueId": 1,
            "homeId": 21, "homeTeam": "San Diego State",
            "homeConference": "Mountain West", "homePoints": None,
            "awayId": 68, "awayTeam": "Boise State",
            "awayConference": "Mountain West", "awayPoints": None,
        })])
        repository.replace_players(2026, tuple(
            Player(f"p{index}", 2026, "Test", f"Player{index}", "Boise State",
                   "QB", index, 74, 200, 3) for index in range(12)))
        ContentRepository(self.path).initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": repository, "CFB_DEFAULT_SEASON": 2026,
            "CFB_DATABASE_PATH": self.path,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        forget_initialized_schemas()
        for path in (self.path, self.path + "-wal", self.path + "-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def _body(self) -> str:
        return self.client.get("/college-football/teams/68/").get_data(as_text=True)

    def test_no_cell_carries_a_dead_label_attribute(self):
        self.assertNotIn("data-label", self._body())

    def test_a_sort_value_is_only_emitted_when_it_differs_from_the_cell(self):
        """The sort script already falls back to the cell's own text."""
        body = self._body()
        pairs = re.findall(r'data-sort-value="([^"]*)">(?:<[^>]+>)*([^<]*)', body)
        repeated = [(value, text) for value, text in pairs
                    if value.strip() == text.strip()]
        self.assertEqual(repeated, [])

    def test_column_keys_outside_the_allowlist_are_not_emitted(self):
        body = self._body()
        emitted = set(re.findall(r"col-key-([a-z0-9-]+)", body))
        with open(STYLESHEET, encoding="utf-8") as handle:
            targeted = {name.removeprefix("col-key-")
                        for name in re.findall(r"\.(col-key-[a-z-]+)", handle.read())}
        self.assertEqual(emitted - targeted, set(),
                         "emitting column keys the stylesheet never selects")

    def test_tables_still_sort_and_label_their_columns(self):
        """Trimming must not take the behaviour with it."""
        body = self._body()
        self.assertIn('data-sortable="true"', body)
        self.assertIn('aria-sort="none"', body)
        self.assertIn("<th scope=\"col\"", body)


if __name__ == "__main__":
    unittest.main()


class PanelSizingTests(unittest.TestCase):
    """A panel should render what it can show, not what it has.

    The dashboard draft panel is capped at 31rem — about twelve dense rows —
    and was rendering the whole hundred-player consensus board into it: a
    scrollbar over ninety hidden rows, and 89 KB of a 303 KB page to display
    twelve of them.
    """

    def test_the_draft_panel_renders_only_what_fits(self):
        from sports_aggregator.cfb.views import DRAFT_PANEL_ROWS, draft_panel_table

        entries = [{"rank": index, "player_name": f"Player {index}",
                    "team_school": "Team", "profile_percentile": 0.5}
                   for index in range(100)]
        table = draft_panel_table(entries, 2026)
        self.assertEqual(len(table.rows), DRAFT_PANEL_ROWS)

    def test_the_panel_says_it_is_showing_a_slice(self):
        from sports_aggregator.cfb.views import draft_panel_table

        entries = [{"rank": index, "player_name": f"Player {index}",
                    "team_school": "Team", "profile_percentile": 0.5}
                   for index in range(100)]
        self.assertIn("of 100", draft_panel_table(entries, 2026).note or "")

    def test_a_short_board_is_not_labelled_as_a_slice(self):
        from sports_aggregator.cfb.views import draft_panel_table

        entries = [{"rank": 1, "player_name": "Only One", "team_school": "Team",
                    "profile_percentile": 0.9}]
        table = draft_panel_table(entries, 2026)
        self.assertEqual(len(table.rows), 1)
        self.assertIsNone(table.note)

    def test_the_panel_offers_a_way_to_the_rows_it_is_not_showing(self):
        """Capping the panel is only honest if the rest stays reachable.

        The cap replaced a scroll region that held all hundred rows, and with
        no link the other eighty-eight were not reachable by clicking at all.
        """
        template = os.path.join(os.path.dirname(TABLE_MACROS), "cfb_today.html")
        with open(template, encoding="utf-8") as handle:
            markup = handle.read()
        panel = markup[markup.index("2027 Draft Watch"):]
        panel = panel[:panel.index("</div>", panel.index("panel-body"))]
        self.assertIn("cfb.draft_watch", panel)

    def test_narrow_tables_are_marked_to_fit_their_container(self):
        """Tables that overflow by a little should not force a scrollbar."""
        from sports_aggregator.cfb.views import draft_panel_table

        with open(TABLE_MACROS, encoding="utf-8") as handle:
            macros = handle.read()
        self.assertIn("NARROW_COLUMN_LIMIT", macros)
        self.assertIn("' fits' if", macros)
        table = draft_panel_table([], 2026)
        self.assertLessEqual(len(table.columns), 8)
