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
