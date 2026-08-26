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

from app import create_app

from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.views import arrivals_of_kind
from sports_aggregator.cfb.recruiting import (
    evidence_basis, evidence_score, rating_strength,
)
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
        # The basis names the season, not the share: the share has its own
        # sortable column beside it, and repeating it filled every row with a
        # number already on screen.
        self.assertIn("prior season", row["blend_basis"])
        self.assertNotIn("%", row["blend_basis"])

    def test_an_unknown_team_yields_nothing_rather_than_raising(self):
        self.assertEqual(
            unit_continuity(self.repository, 999,
                            prior_season=self.PRIOR, current_season=self.CURRENT), {})


if __name__ == "__main__":
    unittest.main()


class RecruitingEvidenceTests(unittest.TestCase):
    """Ranking a recruiting rating against a college grade.

    The `recruits` table was synced and read by nothing, so a five-star ranked
    tenth in the country sat fourth on his own depth chart behind three backups
    in the 7th, 12th and 29th percentiles of graded players, and never appeared
    under key arrivals because transfers were ordered ahead of signees by
    category rather than by quality.
    """

    def test_an_elite_recruit_outranks_a_barely_graded_backup(self):
        """The case that was visibly wrong on the page."""
        five_star = evidence_score(pff_interest=None, recruit_rating=0.9939)
        backup = evidence_score(pff_interest=30.3, recruit_rating=None)
        self.assertGreater(five_star, backup)

    def test_a_genuine_starter_still_outranks_an_elite_recruit(self):
        """The case that would be wrong if rating simply won."""
        starter = evidence_score(pff_interest=85.0, recruit_rating=None)
        five_star = evidence_score(pff_interest=None, recruit_rating=0.9939)
        self.assertGreater(starter, five_star)

    def test_production_wins_at_equal_standing(self):
        """Projection is discounted; a grade describes someone who has played."""
        rating = 0.875                      # mid-range recruiting
        graded = evidence_score(pff_interest=50.0, recruit_rating=None)
        projected = evidence_score(pff_interest=None, recruit_rating=rating)
        self.assertAlmostEqual(rating_strength(rating), 0.5, places=3)
        self.assertGreater(graded, projected)

    def test_a_second_signal_adds_rather_than_diluting(self):
        """Averaging would punish an elite recruit for also having a thin grade."""
        both = evidence_score(pff_interest=20.0, recruit_rating=0.99)
        rating_only = evidence_score(pff_interest=None, recruit_rating=0.99)
        self.assertGreater(both, rating_only)

    def test_a_player_with_no_evidence_scores_zero(self):
        self.assertEqual(evidence_score(pff_interest=None, recruit_rating=None), 0.0)
        self.assertIsNone(evidence_basis(pff_interest=None, recruit_rating=None,
                                         stars=None))

    def test_the_basis_names_which_evidence_placed_him(self):
        self.assertEqual(
            evidence_basis(pff_interest=None, recruit_rating=0.99, stars=5),
            "5-star rating")
        self.assertEqual(
            evidence_basis(pff_interest=80.0, recruit_rating=0.80, stars=3),
            "prior-season grade")

    def test_the_basis_does_not_call_a_transfer_a_signee(self):
        """Transfers carry a high-school rating too."""
        self.assertNotIn("signee",
                         evidence_basis(pff_interest=None, recruit_rating=0.9, stars=4))

    def test_ratings_outside_the_expected_band_are_clamped(self):
        self.assertEqual(rating_strength(0.5), 0.0)
        self.assertEqual(rating_strength(1.5), 1.0)
        self.assertEqual(rating_strength(None), 0.0)


class ProductionEvidenceTests(unittest.TestCase):
    """Production belongs in the ranking, and outranks a projection.

    Adding recruiting evidence without it swung the board the other way: across
    twenty-five teams a three-star signee sat above a back who ran for 788
    yards, and seventy-seven more like it, because a player with no PFF grade
    scored zero however much he had actually done.
    """

    def test_a_productive_player_outranks_a_signee(self):
        """The case that broke when recruiting was added on its own."""
        back = evidence_score(pff_interest=None, recruit_rating=None, production=0.947)
        signee = evidence_score(pff_interest=None, recruit_rating=0.85)
        self.assertGreater(back, signee)

    def test_an_elite_recruit_still_outranks_a_marginal_producer(self):
        """A five-star should not sit behind eighteen rushing yards."""
        five_star = evidence_score(pff_interest=None, recruit_rating=0.9939)
        marginal = evidence_score(pff_interest=None, recruit_rating=None, production=0.30)
        self.assertGreater(five_star, marginal)

    def test_the_strongest_signal_sets_the_floor(self):
        """A weak second signal can lift a player, never lower him."""
        for kwargs in (dict(pff_interest=20.0, recruit_rating=None, production=0.80),
                       dict(pff_interest=80.0, recruit_rating=None, production=0.20)):
            with self.subTest(**kwargs):
                self.assertGreaterEqual(evidence_score(**kwargs), 0.80)

    def test_doing_it_twice_outranks_doing_it_once(self):
        """The point of blending: corroboration counts for something."""
        both = evidence_score(pff_interest=90.0, recruit_rating=None, production=0.9)
        produced_only = evidence_score(pff_interest=None, recruit_rating=None,
                                       production=0.9)
        graded_only = evidence_score(pff_interest=90.0, recruit_rating=None)
        self.assertGreater(both, produced_only)
        self.assertGreater(both, graded_only)

    def test_a_blended_score_never_exceeds_the_ceiling(self):
        """Every signal at maximum still lands inside the scale."""
        self.assertLessEqual(
            evidence_score(pff_interest=100.0, recruit_rating=1.0, production=1.0), 1.0)

    def test_corroboration_is_bounded_rather_than_independent(self):
        """Grade and production restate the same season, so pay once, not twice.

        Treating them as independent confirmations would take two mid signals
        close to certainty; the bonus keeps that in proportion.
        """
        blended = evidence_score(pff_interest=60.0, recruit_rating=None, production=0.6)
        self.assertGreater(blended, 0.6)
        self.assertLess(blended, 0.85)

    def test_the_basis_credits_production_when_it_decides(self):
        self.assertEqual(
            evidence_basis(pff_interest=None, recruit_rating=0.85, stars=3,
                           production=0.94),
            "prior-season production")

    def test_the_basis_still_credits_a_rating_when_that_decides(self):
        self.assertEqual(
            evidence_basis(pff_interest=None, recruit_rating=0.9939, stars=5,
                           production=0.10),
            "5-star rating")

    def test_a_player_with_nothing_on_record_scores_zero(self):
        self.assertEqual(
            evidence_score(pff_interest=None, recruit_rating=None, production=None), 0.0)
        self.assertIsNone(
            evidence_basis(pff_interest=None, recruit_rating=None, stars=None,
                           production=0.0))

    def test_production_is_ranked_within_its_own_category(self):
        """Categories are not comparable: 788 rushing yards is elite, 788
        passing yards is a backup's season."""
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            repository = CFBRepository(path)
            repository.initialize()
            with repository.transaction() as connection:
                for index in range(100):
                    connection.execute(
                        """INSERT INTO player_season_stats(season,player_id,player,team,
                           position,category,stat_type,stat_value,numeric_value)
                           VALUES(2025,?,?,'T','RB','rushing','YDS',?,?)""",
                        (f"r{index}", f"Back {index}", str(index * 10), index * 10))
                    connection.execute(
                        """INSERT INTO player_season_stats(season,player_id,player,team,
                           position,category,stat_type,stat_value,numeric_value)
                           VALUES(2025,?,?,'T','QB','passing','YDS',?,?)""",
                        (f"q{index}", f"Passer {index}", str(index * 40), index * 40))
            rushing = repository.production_strength("rushing", 800, 2025)
            passing = repository.production_strength("passing", 800, 2025)
            self.assertGreater(rushing, passing)
            self.assertEqual(repository.production_strength("rushing", 0, 2025), 0.0)
            self.assertEqual(repository.production_strength("nonsense", 500, 2025), 0.0)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class ArrivalOrderingTests(unittest.TestCase):
    """Portal additions and signees are split, and each ranked by rating.

    The two ratings are the same CFBD recruiting composite on the same scale —
    4-star transfers run 0.90-0.97 against 0.89-0.98 for 4-star signees, and
    3-stars are 0.80-0.89 in both — so they compare directly and a merged list
    is correctly ordered. It is still the wrong presentation: a strong signing
    class fills the table and the portal disappears, which is true and unhelpful.
    Splitting keeps the honest ordering and shows both.
    """

    @staticmethod
    def _rows():
        return [
            {"name": "Five Star", "movement_type": "SIGNEE", "rating": 0.99},
            {"name": "Good Transfer", "movement_type": "TRANSFER_IN", "rating": 0.94},
            {"name": "Four Star", "movement_type": "SIGNEE", "rating": 0.93},
            {"name": "Depth Transfer", "movement_type": "TRANSFER_IN", "rating": 0.85},
            {"name": "Walk On", "movement_type": "NEWCOMER", "rating": None},
        ]

    def test_the_portal_view_excludes_signees(self):
        names = [row["name"] for row in
                 arrivals_of_kind(self._rows(), ("TRANSFER_IN", "NEWCOMER"))]
        self.assertEqual(names, ["Good Transfer", "Depth Transfer", "Walk On"])

    def test_the_signing_view_excludes_transfers(self):
        names = [row["name"] for row in arrivals_of_kind(self._rows(), ("SIGNEE",))]
        self.assertEqual(names, ["Five Star", "Four Star"])

    def test_splitting_preserves_the_incoming_order(self):
        """Each side keeps the rating order the repository already applied."""
        rows = self._rows()
        portal = arrivals_of_kind(rows, ("TRANSFER_IN",))
        self.assertEqual([row["rating"] for row in portal], [0.94, 0.85])

    def test_an_unknown_kind_is_simply_absent_rather_than_raising(self):
        self.assertEqual(arrivals_of_kind(self._rows(), ("DRAFTED",)), [])

    def test_no_arrival_is_lost_across_the_two_views(self):
        rows = self._rows()
        both = (arrivals_of_kind(rows, ("TRANSFER_IN", "NEWCOMER"))
                + arrivals_of_kind(rows, ("SIGNEE",)))
        self.assertEqual(len(both), len(rows))


class MovementColumnTests(unittest.TestCase):
    """Roster movement measures the player, not the classification.

    The Evidence column repeated on every row how the label was reached --
    "CFBD transfer portal", "Roster comparison" -- which is provenance for the
    label rather than anything about the player. The section note says it once
    now, and the width goes to a rating and an impact score.
    """

    def _table(self, *, arrivals, rows):
        from sports_aggregator.cfb import views

        app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False})
        with app.test_request_context():
            return views.movements_table(rows, 2026, arrivals=arrivals)

    def _row(self, **overrides):
        row = {"name": "A Player", "player_id": "1", "position": "WR",
               "movement_type": "TRANSFER_IN", "origin": "Elsewhere",
               "destination": "Elsewhere", "evidence": "CFBD transfer portal",
               "rating": 0.921, "movement_evidence": 0.673,
               "production_strength": 0.5, "pff_interest": 71.0}
        row.update(overrides)
        return row

    def test_the_evidence_column_is_gone(self):
        for arrivals in (True, False):
            with self.subTest(arrivals=arrivals):
                table = self._table(arrivals=arrivals, rows=[self._row()])
                labels = [column.label for column in table.columns]
                self.assertNotIn("Evidence", labels)
                self.assertEqual(labels[-2:], ["Rating", "Impact"])

    def test_no_row_still_carries_the_evidence_text(self):
        table = self._table(arrivals=True, rows=[self._row()])
        self.assertNotIn("CFBD transfer portal", str(table.rows[0].values()))

    def test_impact_is_the_blended_score_on_a_hundred_point_scale(self):
        table = self._table(arrivals=True, rows=[self._row()])
        self.assertEqual(table.rows[0]["impact"], 67.3)
        self.assertEqual(table.rows[0]["rating"], 0.921)

    def test_nothing_on_record_is_blank_rather_than_zero(self):
        """A zero would read as a measurement; this is the absence of one."""
        table = self._table(arrivals=True, rows=[self._row(
            rating=None, movement_evidence=0.0, production_strength=0.0,
            pff_interest=None)])
        self.assertIsNone(table.rows[0]["impact"])

    def test_a_departure_is_measured_the_same_way(self):
        table = self._table(arrivals=False, rows=[self._row(
            movement_type="DRAFTED", rating=None, movement_evidence=0.993)])
        self.assertEqual(table.rows[0]["impact"], 99.3)
        self.assertIsNone(table.rows[0]["rating"])


class PhilosophyWidthTests(unittest.TestCase):
    """It renders in an aside a third of the page wide."""

    def test_the_grade_detail_is_one_facet_not_three(self):
        from sports_aggregator.cfb.views import position_philosophy_table

        table = position_philosophy_table([{
            "position_group": "LB", "tackles": 376, "tackles_share": 40.2,
            "pff_grade": 70.8,
            "pff_detail": "coverage 70.8; defense 70.2; pass rush 64.2",
        }], 2026)
        self.assertEqual(table.rows[0]["pff_grade_sub"], "coverage 70.8")

    def test_no_cell_label_is_long_enough_to_set_a_wide_column(self):
        from sports_aggregator.cfb.views import position_philosophy_table

        table = position_philosophy_table([
            {"position_group": group, "pff_grade": 70.0, "pff_detail": None}
            for group in ("QB", "RB", "WR", "TE", "OL", "DL", "EDGE", "LB", "SECONDARY")
        ], 2026)
        for row in table.rows:
            with self.subTest(group=row["group"]):
                self.assertLessEqual(len(row["production_sub"] or ""), 20)

    def test_a_missing_detail_stays_missing(self):
        from sports_aggregator.cfb.views import position_philosophy_table

        table = position_philosophy_table(
            [{"position_group": "QB", "pff_grade": None, "pff_detail": None}], 2026)
        self.assertIsNone(table.rows[0]["pff_grade_sub"])
