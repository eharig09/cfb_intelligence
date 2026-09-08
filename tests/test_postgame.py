"""Postgame analysis should explain evidence, not manufacture a narrative."""

from __future__ import annotations

import unittest

from sports_aggregator.cfb.postgame import decisive_factors, player_impacts


class PostgameFactorTests(unittest.TestCase):
    def _rows(self):
        values = {
            "Michigan": {
                "totalYards": 455, "yardsPerPass": 9.1, "yardsPerRushAttempt": 5.7,
                "firstDowns": 25, "thirdDownEff": "8/13", "turnovers": 0,
                "sacks": 4, "tacklesForLoss": 7, "qbHurries": 6,
                "totalPenaltiesYards": "4-35", "possessionTime": "34:20",
            },
            "Ohio State": {
                "totalYards": 326, "yardsPerPass": 6.2, "yardsPerRushAttempt": 3.8,
                "firstDowns": 17, "thirdDownEff": "3/12", "turnovers": 2,
                "sacks": 1, "tacklesForLoss": 3, "qbHurries": 2,
                "totalPenaltiesYards": "7-68", "possessionTime": "25:40",
            },
        }
        return [
            {"team": team, "category": category, "numeric_value": value if isinstance(value, (int, float)) else None,
             "stat_value": None if isinstance(value, (int, float)) else value}
            for team, stats in values.items() for category, value in stats.items()
        ]

    def test_real_separators_are_ranked(self):
        game = {"away_team": "Ohio State", "home_team": "Michigan",
                "away_points": 17, "home_points": 31}
        factors, _coverage = decisive_factors(game, self._rows())
        keys = {factor["key"] for factor in factors}
        self.assertIn("turnovers", keys)
        self.assertIn("pass_efficiency", keys)
        self.assertEqual(factors[0]["winner"], "Michigan")

    def test_small_difference_is_not_called_decisive(self):
        rows = [
            {"team": "A", "category": "yardsPerRushAttempt", "numeric_value": 4.5},
            {"team": "B", "category": "yardsPerRushAttempt", "numeric_value": 4.2},
        ]
        factors, _coverage = decisive_factors(
            {"away_team": "A", "home_team": "B", "away_points": 24, "home_points": 21}, rows)
        self.assertNotIn("rush_efficiency", {factor["key"] for factor in factors})

    def test_player_impact_uses_actual_box_stats(self):
        rows = [
            {"team": "Michigan", "player": "QB One", "player_id": "1",
             "category": "passing", "stat_type": "YDS", "numeric_value": 310},
            {"team": "Michigan", "player": "QB One", "player_id": "1",
             "category": "passing", "stat_type": "TD", "numeric_value": 3},
            {"team": "Ohio State", "player": "RB Two", "player_id": "2",
             "category": "rushing", "stat_type": "YDS", "numeric_value": 80},
        ]
        impacts = player_impacts(rows)
        self.assertEqual(impacts[0]["player"], "QB One")
        self.assertIn("310 pass yds", impacts[0]["summary"])




class TurningPointPlayerTests(unittest.TestCase):
    """Names in a turning point are resolved against the roster, then linked."""

    def index(self):
        return {"jalen milroe": ("4432734", "Alabama"),
                "milroe,jalen": ("4432734", "Alabama"),
                "carson beck": ("4685720", "Georgia")}

    def matches(self, text, index=None):
        from sports_aggregator.cfb.postgame_analytics_display import _play_pattern
        pattern = _play_pattern(self.index() if index is None else index)
        return [(m.group(0), bool(m.groupdict().get("roster")))
                for m in pattern.finditer(text)]

    def test_a_full_name_is_recognised(self):
        """The provider writes "Jalen Milroe"; the old pattern only knew "#12 Milroe"."""
        self.assertIn(("Jalen Milroe", True),
                      self.matches("Jalen Milroe pass complete to the ALA 38"))

    def test_the_comma_form_is_recognised_too(self):
        self.assertIn(("Milroe,Jalen", True),
                      self.matches("Shotgun Milroe,Jalen pass complete short left"))

    def test_a_roster_name_matches_whatever_case_the_provider_used(self):
        self.assertIn(("JALEN MILROE", True), self.matches("JALEN MILROE pass complete"))

    def test_the_generic_pattern_stays_case_sensitive(self):
        """[A-Z] has to mean a capital.

        Compiling the whole pattern case-insensitively matched "by" in
        "#1 by MSH." and highlighted it as a player, on 69 plays in 60,000.
        """
        self.assertEqual(
            [name for name, _roster in self.matches("Kickoff returned #1 by MSH.")], [])

    def test_the_jersey_and_initial_forms_still_match(self):
        found = [name for name, _roster in self.matches("R.Spruill rushed. Tackled by W.Philord")]
        self.assertEqual(found, ["R.Spruill", "W.Philord"])

    def test_without_a_roster_it_falls_back_to_the_shape_of_a_name(self):
        from sports_aggregator.cfb.postgame_analytics_display import _PLAYER
        from sports_aggregator.cfb.postgame_analytics_display import _play_pattern
        self.assertIs(_play_pattern({}), _PLAYER)

    def test_a_name_two_players_share_is_left_unlinked(self):
        """Better an unlinked name than a link to the wrong player."""
        shared = {"jay williams": ("1", "Alabama")}
        self.assertEqual(
            [roster for _name, roster in self.matches("Jay Williams run", shared)], [True])


if __name__ == "__main__":
    unittest.main()


class TurningPointRenderTests(unittest.TestCase):
    """The compact turning-point card: consistent player colour, a WP bar and a
    field strip drawn from structured numbers rather than parsed team codes."""

    def _row(self, **over):
        base = dict(
            event_yards_to_goal=40, yards_to_goal=40, yards_gained=40,
            event_down=2, down=2, event_distance=6, distance=6,
            event_offense="San José State", offense="San José State",
            event_defense="Eastern Michigan", defense="Eastern Michigan",
            home_wp_before=0.643, home_wp_after=0.336,
            play_type="Pass", play_text="pass complete deep middle TOUCHDOWN",
            scoring=1, event_priority=100,
        )
        base.update(over)
        return base

    def test_a_touchdown_drive_is_drawn_to_the_end_zone(self):
        from sports_aggregator.cfb.postgame_analytics_display import _field_svg
        svg = _field_svg(self._row())
        self.assertIn("pg-f-target scored", svg)      # end zone lit
        self.assertIn('class="pg-f-drive td"', svg)   # the drive bar

    def test_a_turnover_is_drawn_as_a_marker_not_a_drive(self):
        from sports_aggregator.cfb.postgame_analytics_display import _field_svg
        svg = _field_svg(self._row(play_text="fumble recovered TOUCHDOWN", scoring=1))
        self.assertIn("pg-f-turnover", svg)
        self.assertNotIn('class="pg-f-drive td"', svg)

    def test_the_wp_bar_is_tinted_for_the_team_the_swing_helped(self):
        from sports_aggregator.cfb.postgame_analytics_display import _wp_meter_html
        html = _wp_meter_html(self._row(), "Eastern Michigan", "San José State")
        # home WP fell 64 -> 34, so the swing helped the away team
        self.assertIn("pts to San José State", html)
        self.assertIn("64%", html); self.assertIn("34%", html)

    def test_a_player_named_twice_in_one_play_keeps_one_colour(self):
        from app import create_app
        from sports_aggregator.cfb.postgame_analytics_display import _play_html
        index = {"b.llewellyn": ("99", "Eastern Michigan")}
        colors = {"Eastern Michigan": "#298055", "San José State": "#266eaf"}
        text = ("recovered by EMU #6 B.Llewellyn at the 1 "
                "#6 B.Llewellyn return 1 yard TOUCHDOWN")
        with create_app({"TESTING": True}).test_request_context("/"):
            html = _play_html(text, {"season": 2026}, colors,
                              "San José State", "Eastern Michigan", index)
        # the recovering defender is linked and EMU-coloured at both mentions;
        # the second used to fall through to the offence colour before the fix.
        self.assertEqual(html.count("/college-football/players/99/"), 2)
        self.assertEqual(html.count("#298055"), 2)
        self.assertNotIn("#266eaf", html)

    def test_field_codes_use_the_real_abbreviation_not_a_guess(self):
        from sports_aggregator.cfb.postgame_analytics_display import _humanize_field_codes
        game = {"away_team": "San José State", "home_team": "Eastern Michigan"}
        out = _humanize_field_codes("caught at EMU16, advanced to EMU00 TOUCHDOWN", game,
                                    {"EMU": "Eastern Michigan", "SJSU": "San José State"})
        self.assertIn("Eastern Michigan 16", out)
        self.assertIn("Eastern Michigan goal line", out)
        self.assertNotIn("EMU16", out)
