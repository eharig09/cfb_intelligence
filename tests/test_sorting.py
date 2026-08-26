"""Columns that read as words and rank as numbers.

A stat line is text to read -- "23 car / 118 yd / 1 TD" -- and a number to rank
by. Sorted as text it is compared character by character, so " " beats "3" and
the column comes out 1, 1, 2, 23, 3, 3, 4. Display and order are separate
questions, and these tests hold them apart.

The other half is the opposite case: a table with one metric per row, each on
its own scale, has no order at all, and a sort control that cannot mean
anything is worse than no control.
"""

from __future__ import annotations

import re
import unittest

from app import create_app
from sports_aggregator.cfb import views
from sports_aggregator.tables import Column, Table


def _application():
    """The real application: the macro uses its filters and url_for."""
    return create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
                       "CFB_DEFAULT_SEASON": 2026})


class ColumnSortKindTests(unittest.TestCase):

    def test_a_text_column_sorts_as_text_by_default(self):
        self.assertEqual(Column("player", "Player").sort_kind, "text")

    def test_a_formatted_number_sorts_as_a_number_by_default(self):
        self.assertEqual(Column("yards", "Yds", format="int").sort_kind, "number")

    def test_a_text_column_can_be_told_to_rank_numerically(self):
        column = Column("rushing", "Rushing", sort="number")
        self.assertEqual(column.sort_kind, "number")

    def test_asking_to_sort_numerically_does_not_restyle_the_column(self):
        """A stat line is still prose; it must not become tabular digits."""
        column = Column("rushing", "Rushing", sort="number")
        self.assertFalse(column.numeric)
        self.assertEqual(column.align, "left")

    def test_an_unknown_sort_kind_is_refused_rather_than_ignored(self):
        with self.assertRaises(ValueError):
            Column("rushing", "Rushing", sort="numeric")

    def test_the_json_api_says_how_a_column_orders(self):
        payload = Table(columns=[Column("rushing", "Rushing", sort="number")]).as_dict()
        self.assertEqual(payload["columns"][0]["sort"], "number")


class LeadingNumberTests(unittest.TestCase):
    """The figure a line leads with is the one it is ranked on."""

    def test_the_first_number_is_taken_from_a_stat_line(self):
        self.assertEqual(views.leading_number("23 car / 118 yd / 1 TD"), 23.0)
        self.assertEqual(views.leading_number("9 rec / 136 yd"), 9.0)

    def test_a_decimal_survives(self):
        self.assertEqual(views.leading_number("1.5 sacks"), 1.5)

    def test_a_line_with_no_number_has_no_order(self):
        for value in (None, "", "—", "Box"):
            with self.subTest(value=value):
                self.assertIsNone(views.leading_number(value))

    def test_the_reported_case_orders_by_value_not_by_first_digit(self):
        """1, 1, 2, 23, 3, 3, 4 was the bug, in that order."""
        lines = ["3 car / 12 yd", "23 car / 118 yd", "1 car / 2 yd",
                 "4 car / 30 yd", "2 car / 9 yd"]
        ordered = sorted(lines, key=views.leading_number, reverse=True)
        self.assertEqual([views.leading_number(line) for line in ordered],
                         [23.0, 4.0, 3.0, 2.0, 1.0])


class RecordOrderTests(unittest.TestCase):

    def test_wins_lead(self):
        self.assertGreater(views.record_order("10-2"), views.record_order("9-3"))

    def test_ten_wins_does_not_land_between_one_and_two(self):
        """Which is where a text sort puts it."""
        records = ["1-11", "10-2", "2-10"]
        self.assertEqual(sorted(records, key=views.record_order),
                         ["1-11", "2-10", "10-2"])

    def test_the_win_share_separates_equal_win_totals(self):
        self.assertGreater(views.record_order("5-2"), views.record_order("5-7"))

    def test_something_that_is_not_a_record_has_no_order(self):
        for value in (None, "", "—", "5", "5-", "abc"):
            with self.subTest(value=value):
                self.assertIsNone(views.record_order(value))

    def test_an_unplayed_season_does_not_divide_by_zero(self):
        self.assertEqual(views.record_order("0-0"), 0.0)


class BuiltTableTests(unittest.TestCase):
    """The tables the audit found, checked at the column definition."""

    def setUp(self):
        self.context = _application().test_request_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def _column(self, table: Table, key: str) -> Column:
        return next(column for column in table.columns if column.key == key)

    def test_prior_game_stat_lines_rank_numerically(self):
        table = views.opponent_performance_table([{
            "player": "Name", "player_id": "1", "season": 2025,
            "date_label": "Sep 01, 2025", "opponent": "Other", "team": "Team",
            "passing": "18/25 / 245 yd", "rushing": "23 car / 118 yd",
            "receiving": "9 rec / 136 yd", "defense": "6 tkl / 1 INT",
            "game_url": "/x/",
        }])
        for key in ("passing", "rushing", "receiving", "defense"):
            with self.subTest(column=key):
                self.assertEqual(self._column(table, key).sort_kind, "number")
        row = table.rows[0]
        self.assertEqual(row["rushing_sort"], 23.0)
        self.assertEqual(row["receiving_sort"], 9.0)
        self.assertEqual(row["defense_sort"], 6.0)
        self.assertEqual(row["passing_sort"], 18.0)

    def test_a_missing_line_carries_no_order(self):
        table = views.opponent_performance_table([{
            "player": "Name", "player_id": "1", "season": 2025,
            "date_label": "Sep 01, 2025", "opponent": "Other", "team": "Team",
            "passing": None, "rushing": None, "receiving": None, "defense": None,
            "game_url": "/x/",
        }])
        self.assertIsNone(table.rows[0]["rushing_sort"])

    def test_a_season_record_ranks_by_wins(self):
        table = views.season_history_table([
            {"season": 2025, "record": "10-2"}, {"season": 2024, "record": "2-10"}])
        self.assertEqual(self._column(table, "record").sort_kind, "number")
        self.assertGreater(table.rows[0]["record_sort"], table.rows[1]["record_sort"])

    def test_a_game_log_score_ranks_by_points_scored(self):
        table = views.historical_games_table([
            {"season": 2025, "date_label": "Sep 01", "opponent": "Other",
             "result": "W", "score": "35-31", "game_id": 1}])
        self.assertEqual(self._column(table, "score").sort_kind, "number")
        self.assertEqual(table.rows[0]["score_sort"], 35.0)

    def test_a_roster_height_ranks_by_inches(self):
        table = views.roster_table([
            {"player_id": "1", "first_name": "A", "last_name": "B", "position": "QB",
             "jersey": 1, "height": 75, "weight": 210, "year": 3}], 2026)
        self.assertEqual(self._column(table, "height").sort_kind, "number")

    def test_a_key_value_table_offers_no_sort_at_all(self):
        """Each row is its own scale; there is no order to put them in."""
        table = views.quality_cards_table({"cards": [
            {"label": "Returning production", "value": 14.4, "format": "pct",
             "source": "CFBD"},
            {"label": "Transfer arrivals", "value": 35, "format": "int",
             "source": "CFBD"}]})
        self.assertFalse(table.sortable)


class MacroTests(unittest.TestCase):
    """What the header and the cells actually carry."""

    SOURCE = '{% from "_tables.html" import data_table %}{{ data_table(table) }}'

    def setUp(self):
        self.app = _application()

    def _render(self, table):
        with self.app.test_request_context():
            return self.app.jinja_env.from_string(self.SOURCE).render(table=table)

    def _table(self, **kwargs):
        return Table(
            columns=[Column("player", "Player"),
                     Column("rushing", "Rushing", sort="number")],
            rows=[{"player": "A", "rushing": "23 car / 118 yd", "rushing_sort": 23.0},
                  {"player": "B", "rushing": "3 car / 12 yd", "rushing_sort": 3.0},
                  {"player": "C", "rushing": None, "rushing_sort": None}],
            **kwargs)

    def test_the_header_declares_the_numeric_sort(self):
        markup = self._render(self._table())
        header = re.search(r"<th[^>]*>Rushing</th>", markup).group(0)
        self.assertIn('data-sort-kind="number"', header)
        self.assertIn('data-sortable="true"', header)

    def test_each_cell_carries_the_number_it_ranks_by(self):
        markup = self._render(self._table())
        self.assertIn('data-sort-value="23.0"', markup)
        self.assertIn('data-sort-value="3.0"', markup)

    def test_a_cell_with_no_line_carries_no_sort_value(self):
        """The script treats a missing value as missing and files it last."""
        markup = self._render(self._table())
        rows = re.findall(r"<tr>(.*?)</tr>", markup, re.S)
        self.assertNotIn("data-sort-value", rows[-1])

    def test_an_unsortable_table_offers_no_control(self):
        markup = self._render(self._table(sortable=False))
        headers = re.findall(r"<th[^>]*>", markup, re.S)
        self.assertTrue(headers)
        for header in headers:
            with self.subTest(header=header):
                self.assertNotIn("data-sortable", header)
                self.assertNotIn("aria-sort", header)
                self.assertNotIn("tabindex", header)

    def test_an_unsortable_header_keeps_its_own_title(self):
        """Only the "activate to sort" half goes away."""
        table = Table(columns=[Column("m", "Measure", title="What this is")],
                      rows=[{"m": "x"}], sortable=False)
        markup = self._render(table)
        self.assertIn('title="What this is"', markup)
        self.assertNotIn("activate to sort", markup)


if __name__ == "__main__":
    unittest.main()
