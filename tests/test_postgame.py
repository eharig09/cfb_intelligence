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


if __name__ == "__main__":
    unittest.main()
