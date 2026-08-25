"""Unit continuity and grade blending.

The trap this module exists around is subtle enough to be worth restating: PFF's
stored ``cfbd_player_id`` is written by matching against the roster of the
season being imported, so a player who has since left is unresolved. Deciding
continuity from that link treats every departure as unknown, which does not bias
the answer slightly — it inverts it.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.unit_continuity import (
    PRIOR_CREDIBILITY_GAMES, blend_unit_grade, unit_continuity,
    units_with_continuity,
)


class BlendTests(unittest.TestCase):
    """Credibility weighting, independent of any database."""

    def test_before_a_snap_is_played_the_grade_is_last_season(self):
        blend = blend_unit_grade(prior_grade=78.0, returning_share=0.8)
        self.assertEqual(blend["value"], 78.0)
        self.assertEqual(blend["current_weight"], 0.0)
        self.assertIn("prior season", blend["basis"])

    def test_a_unit_that_returned_nobody_gets_no_credit_for_last_season(self):
        """Last year's grade is evidence about players who have left."""
        blend = blend_unit_grade(prior_grade=78.0, returning_share=0.0,
                                 current_grade=62.0, current_games=1)
        self.assertEqual(blend["value"], 62.0)
        self.assertEqual(blend["prior_weight"], 0.0)

    def test_a_unit_that_returned_nobody_and_has_not_played_has_no_grade(self):
        blend = blend_unit_grade(prior_grade=78.0, returning_share=0.0)
        self.assertIsNone(blend["value"])
        self.assertIn("no grade", blend["basis"])

    def test_current_play_overtakes_the_prior_as_games_accumulate(self):
        values = [blend_unit_grade(prior_grade=78.0, returning_share=1.0,
                                   current_grade=62.0, current_games=games)["value"]
                  for games in (0, 2, 4, 8, 13)]
        self.assertEqual(values[0], 78.0)
        # Monotonically approaching the current grade, never reaching it.
        self.assertTrue(all(later < earlier for earlier, later in zip(values, values[1:])),
                        values)
        self.assertGreater(values[-1], 62.0)

    def test_parity_lands_where_the_constant_says_it_does(self):
        blend = blend_unit_grade(prior_grade=80.0, returning_share=1.0,
                                 current_grade=60.0,
                                 current_games=PRIOR_CREDIBILITY_GAMES)
        self.assertEqual(blend["value"], 70.0)
        self.assertEqual(blend["prior_weight"], 0.5)

    def test_a_thinner_returning_share_reaches_parity_sooner(self):
        """Less of last year's unit remains, so it speaks with less authority."""
        half = blend_unit_grade(prior_grade=80.0, returning_share=0.5,
                                current_grade=60.0,
                                current_games=PRIOR_CREDIBILITY_GAMES / 2)
        self.assertEqual(half["prior_weight"], 0.5)

    def test_a_residual_of_last_season_survives_a_full_season(self):
        """"Keep a little of last year, in proportion to what returned."""
        heavy = blend_unit_grade(prior_grade=80.0, returning_share=0.9,
                                 current_grade=60.0, current_games=13)
        light = blend_unit_grade(prior_grade=80.0, returning_share=0.2,
                                 current_grade=60.0, current_games=13)
        self.assertGreater(heavy["prior_weight"], 0)
        self.assertGreater(heavy["prior_weight"], light["prior_weight"])

    def test_an_unmeasurable_share_says_so_rather_than_assuming_zero(self):
        blend = blend_unit_grade(prior_grade=78.0, returning_share=None)
        self.assertEqual(blend["value"], 78.0)
        self.assertIsNone(blend["returning_share"])
        self.assertIn("could not be measured", blend["basis"])

    def test_shares_outside_the_unit_interval_are_clamped(self):
        for share in (-0.5, 1.5):
            blend = blend_unit_grade(prior_grade=70.0, returning_share=share,
                                     current_grade=60.0, current_games=4)
            self.assertGreaterEqual(blend["returning_share"], 0.0)
            self.assertLessEqual(blend["returning_share"], 1.0)


class ContinuityTests(unittest.TestCase):
    """Classification against the current roster, using stored PFF rows."""

    PRIOR, CURRENT = 2025, 2026

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.initialize()
        self.repository.replace_teams((
            Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None, "fbs",
                 "00274C", "FFCB05", (), ("Michigan",), None, None),
        ))
        # Only the low-snap edge player stays; the high-snap ones leave.
        self.repository.replace_players(self.CURRENT, (
            Player("stay", self.CURRENT, "Nate", "Marshall", "Michigan", "DL", 5, 75, 250, 2),
        ))
        self._store_pff()

    def tearDown(self):
        os.unlink(self.path)

    def _store_pff(self):
        """Two departures with most of the snaps, one returner with few.

        Critically, the departures carry no `cfbd_player_id` — which is exactly
        the state PFF's importer leaves them in once they are off the roster.
        """
        rows = [
            # (pff_id, name, normalized, position, cfbd_player_id, usage, grade)
            ("p1", "Derrick Moore", "derrick moore", "ED", None, 440.0, 75.0),
            ("p2", "Cam Brandt", "cam brandt", "ED", None, 360.0, 72.0),
            ("p3", "Nate Marshall", "nate marshall", "ED", "stay", 60.0, 60.0),
        ]
        with self.repository.transaction() as connection:
            for pff_id, name, normalized, position, cfbd_id, usage, grade in rows:
                connection.execute(
                    """INSERT INTO pff_players(season,pff_player_id,player_name,
                       normalized_name,position,pff_team_name,cfbd_team_id,cfbd_team,
                       cfbd_player_id,candidate_cfbd_player_id,match_status,
                       match_confidence,interest_score,updated_at)
                       VALUES(?,?,?,?,?,'Michigan',1,'Michigan',?,NULL,?,1.0,?, '2026-01-01')""",
                    (self.PRIOR, pff_id, name, normalized, position, cfbd_id,
                     "exact_name_same_team" if cfbd_id else "unresolved", grade))
                connection.execute(
                    """INSERT INTO pff_player_metrics(season,pff_player_id,dataset,
                       source_file,game_count,primary_grade,usage_count,metrics_json,
                       imported_at)
                       VALUES(?,?,'defense','test.csv',12,?,?, '{}', '2026-01-01')""",
                    (self.PRIOR, pff_id, grade, usage))
            connection.execute(
                """INSERT INTO pff_position_groups(season,cfbd_team_id,pff_team_name,
                   position_group,dataset,weighted_grade,player_count,usage_count)
                   VALUES(?,1,'Michigan','EDGE','defense',74.0,3,860.0)""",
                (self.PRIOR,))

    def _edge(self):
        carry = unit_continuity(self.repository, 1,
                                prior_season=self.PRIOR, current_season=self.CURRENT)
        return carry[("defense", "EDGE")]

    def test_departures_are_counted_even_though_pff_never_linked_them(self):
        """The inversion this module exists to prevent."""
        edge = self._edge()
        self.assertEqual(edge["departed_players"], 2)
        self.assertEqual(edge["departed_usage"], 800.0)
        self.assertEqual(edge["returning_players"], 1)

    def test_the_share_is_snap_weighted_not_headcount(self):
        """One of three players returns, but only 7% of the snaps."""
        edge = self._edge()
        self.assertAlmostEqual(edge["returning_share"], 60.0 / 860.0, places=3)
        self.assertLess(edge["returning_share"], 0.1)

    def test_the_denominator_is_the_whole_unit(self):
        edge = self._edge()
        self.assertEqual(edge["total_usage"],
                         edge["returning_usage"] + edge["departed_usage"])

    def test_a_returner_matched_only_by_name_is_reported_as_weaker_evidence(self):
        """Name matching recovers players PFF never linked, at lower confidence."""
        self.repository.replace_players(self.CURRENT, (
            Player("stay", self.CURRENT, "Nate", "Marshall", "Michigan", "DL", 5, 75, 250, 2),
            Player("moore", self.CURRENT, "Derrick", "Moore", "Michigan", "DL", 9, 76, 255, 4),
        ))
        edge = self._edge()
        self.assertEqual(edge["returning_players"], 2)
        self.assertEqual(edge["name_only_usage"], 440.0)
        self.assertGreater(edge["name_only_share"], 0.5)
        self.assertFalse(edge["strongly_matched"])

    def test_the_grade_row_carries_its_continuity_and_a_blended_value(self):
        rows = units_with_continuity(self.repository, 1,
                                     prior_season=self.PRIOR, current_season=self.CURRENT)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["prior_grade"], 74.0)
        # No current-season grades exist, so the adjusted figure is the prior.
        self.assertEqual(row["blended_grade"], 74.0)
        self.assertLess(row["returning_share"], 0.1)
        self.assertIn("returns", row["blend_basis"])

    def test_an_unknown_team_yields_nothing_rather_than_raising(self):
        self.assertEqual(
            unit_continuity(self.repository, 999,
                            prior_season=self.PRIOR, current_season=self.CURRENT), {})


if __name__ == "__main__":
    unittest.main()
