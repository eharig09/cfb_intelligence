import os
import tempfile
import unittest

from app import create_app
from sports_aggregator.cfb import views
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.statlines import leader_table, player_stat_tables
from sports_aggregator.tables import Column, Table, format_value


def stat(season, category, stat_type, value, player="Alex Example",
         team="Michigan", position="QB"):
    return {"season": season, "player_id": "p1", "player": player, "team": team,
            "conference": "Big Ten", "position": position, "category": category,
            "stat_type": stat_type, "stat_value": str(value), "numeric_value": value}


class FormatTests(unittest.TestCase):
    def test_missing_values_render_as_an_em_dash_not_a_zero(self):
        for value in (None, ""):
            self.assertEqual(format_value(value, "int"), "—")
            self.assertEqual(format_value(value, "f1"), "—")

    def test_each_format_uses_its_own_scale(self):
        self.assertEqual(format_value(0.686, "rate"), "68.6%")
        self.assertEqual(format_value(68.6, "pct"), "68.6%")
        self.assertEqual(format_value(3120, "int"), "3120")
        self.assertEqual(format_value(83122, "big"), "83,122")
        self.assertEqual(format_value(0.4521, "f3"), "0.452")
        self.assertEqual(format_value(9.0, "num"), "9")

    def test_non_numeric_values_survive_a_numeric_format(self):
        self.assertEqual(format_value("TBD", "f1"), "TBD")

    def test_numeric_columns_default_to_right_alignment(self):
        self.assertEqual(Column("yards", "YDS", format="int").align, "right")
        self.assertEqual(Column("team", "Team").align, "left")

    def test_table_is_falsey_when_empty_so_templates_can_branch(self):
        self.assertFalse(Table(columns=[Column("a", "A")]))
        self.assertTrue(Table(columns=[Column("a", "A")], rows=[{"a": 1}]))


class StatLineTests(unittest.TestCase):
    def test_long_form_rows_collapse_into_one_row_per_season(self):
        rows = [
            stat(2025, "passing", "YDS", 3120),
            stat(2025, "passing", "ATT", 400),
            stat(2025, "passing", "COMPLETIONS", 270),
            stat(2025, "passing", "TD", 27),
            stat(2024, "passing", "YDS", 2100),
            stat(2024, "passing", "ATT", 300),
        ]
        tables = player_stat_tables(rows)
        self.assertEqual([group["category"] for group in tables], ["passing"])
        table = tables[0]["table"]
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.rows[0]["season"], 2025)
        self.assertEqual(table.rows[0]["YDS"], 3120)
        self.assertEqual(table.rows[0]["COMPLETIONS"], 270)

    def test_columns_follow_box_score_order_not_alphabetical_order(self):
        tables = player_stat_tables([stat(2025, "passing", "YDS", 3120)])
        labels = [column.label for column in tables[0]["table"].columns]
        self.assertEqual(labels, ["Season", "Team", "CMP", "ATT", "PCT", "YDS", "YPA", "TD", "INT"])

    def test_categories_are_ordered_by_reading_convention(self):
        rows = [
            stat(2025, "kicking", "PTS", 90),
            stat(2025, "passing", "YDS", 3120),
            stat(2025, "receiving", "YDS", 400),
        ]
        self.assertEqual(
            [group["category"] for group in player_stat_tables(rows)],
            ["passing", "receiving", "kicking"],
        )

    def test_unknown_category_still_renders_rather_than_disappearing(self):
        tables = player_stat_tables([stat(2025, "brandNewCategory", "XYZ", 5)])
        self.assertEqual(len(tables), 1)
        self.assertIn("XYZ", [column.label for column in tables[0]["table"].columns])

    def test_leaderboard_carries_the_full_stat_line(self):
        table = leader_table("rushing", [
            {"player": "Runner", "player_id": "p2", "position": "RB", "team": "Wisconsin",
             "stats": {"YDS": 1400, "CAR": 240, "TD": 12}},
        ])
        self.assertEqual(table.rows[0]["rank"], 1)
        self.assertEqual(table.rows[0]["CAR"], 240)
        self.assertIn("CAR", [column.key for column in table.columns])


class ViewTableTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
        })

    def tearDown(self):
        os.unlink(self.path)

    def test_height_inches_render_as_feet_and_inches(self):
        self.assertEqual(views.height_label(74), "6-2")
        self.assertEqual(views.height_label(72), "6-0")
        self.assertIsNone(views.height_label(None))
        self.assertIsNone(views.height_label(0))

    def test_schedule_marks_wins_and_losses_from_the_team_perspective(self):
        schedule = [
            {"game_id": 1, "week": 1, "home_team_id": 5, "away_team_id": 9,
             "home_team": "Michigan", "away_team": "Wisconsin", "home_points": 30,
             "away_points": 17, "completed": True, "television": "FOX",
             "date_label": "Sat, Sep 05", "time_label": "12:00 PM"},
            {"game_id": 2, "week": 2, "home_team_id": 9, "away_team_id": 5,
             "home_team": "Wisconsin", "away_team": "Michigan", "home_points": 21,
             "away_points": 14, "completed": True, "television": None,
             "date_label": "Sat, Sep 12", "time_label": "3:30 PM"},
            {"game_id": 3, "week": 3, "home_team_id": 5, "away_team_id": 9,
             "home_team": "Michigan", "away_team": "Wisconsin", "home_points": None,
             "away_points": None, "completed": False, "television": None,
             "date_label": "Sat, Sep 19", "time_label": "TBD"},
        ]
        with self.app.test_request_context():
            table = views.schedule_table(schedule, 5, 2026)
        self.assertEqual(table.rows[0]["result"], "W 30-17")
        self.assertEqual(table.rows[0]["result_class"], "win")
        self.assertEqual(table.rows[0]["site"], "vs")
        self.assertEqual(table.rows[1]["result"], "L 14-21")
        self.assertEqual(table.rows[1]["site"], "at")
        self.assertEqual(table.rows[2]["result_class"], "pending")

    def test_standings_render_records_as_records_not_separate_columns(self):
        standings = [{
            "team_id": 5, "school": "Michigan", "rank": 3, "games": 12,
            "wins": 10, "losses": 2, "ties": 0, "conference_wins": 7,
            "conference_losses": 2, "conference_ties": 0, "expected_wins": 9.4,
        }]
        with self.app.test_request_context():
            table = views.standings_table(standings, 2026)
        self.assertEqual(table.rows[0]["overall_record"], "10-2")
        self.assertEqual(table.rows[0]["conference_record"], "7-2")

    def test_advanced_metrics_keep_a_per_row_scale(self):
        game = {
            "season": 2026, "home_team": "Michigan", "away_team": "Wisconsin",
            "advanced_metrics": {
                "Michigan": {"offense_success_rate": .452, "offense_ppa": .231},
                "Wisconsin": {"offense_success_rate": .401, "offense_ppa": .118},
            },
        }
        table = views.matchup_metrics_table(game)
        by_metric = {row["metric"]: row for row in table.rows}
        self.assertEqual(by_metric["Success rate"]["home_offense"], "45.2%")
        self.assertEqual(by_metric["PPA per play"]["home_offense"], "0.231")
        self.assertEqual(by_metric["Havoc"]["home_offense"], "—")

    def test_camel_case_team_stat_names_become_readable_headers(self):
        metrics = {"stats": [{"stat_name": "thirdDownConversions", "stat_value": 71}]}
        table = views.team_stats_table(metrics, 2026)
        self.assertEqual(table.rows[0]["stat_name"], "Third Down Conversions")


class RenderedTableTests(unittest.TestCase):
    """The pages must emit real tables, not divs styled to look like them."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        self.repository.replace_player_stats(2025, [
            {"season": 2025, "playerId": "p1", "player": "Alex Example",
             "team": "Michigan", "conference": "Big Ten", "position": "QB",
             "category": "passing", "statType": key, "stat": value}
            for key, value in (("YDS", "3120"), ("ATT", "400"),
                               ("COMPLETIONS", "270"), ("TD", "27"), ("PCT", "0.675"))
        ], "Big Ten")
        from sports_aggregator.cfb.models import Player
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Alex", "Example", "Michigan", "QB", 7, 74, 215, 3),
        ))
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
        })

    def tearDown(self):
        os.unlink(self.path)

    def test_player_page_renders_a_pivoted_stat_line(self):
        body = self.app.test_client().get("/college-football/players/p1/").get_data(as_text=True)
        self.assertIn('<table class="data', body)
        for header in (">CMP<", ">ATT<", ">PCT<", ">YDS<", ">TD<"):
            self.assertIn(header, body)
        # Values belong to one row, not five key/value rows.
        self.assertIn("67.5%", body)
        self.assertNotIn("<th>Statistic</th>", body)
