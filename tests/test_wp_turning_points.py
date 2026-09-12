"""A kickoff or punt row carries the kicking team's pre-kick line, never the
return's result -- it should never anchor a turning-point's before/after WP."""

from __future__ import annotations

import unittest

from sports_aggregator.cfb.wp_turning_points import _valid_state


class ValidStateTests(unittest.TestCase):
    def _row(self, **over):
        base = dict(
            period=1, down=1, distance=10, yards_to_goal=65,
            offense="Pittsburgh", defense="Miami (OH)",
            home_win_probability=0.71, play_type="Kickoff",
            play_text="kickoff 65 yards to the Miami00 return 55 yards to the Pitt45",
        )
        base.update(over)
        return base

    def test_a_kickoff_is_never_a_valid_state(self):
        """Its down/distance/yardline describe the kicking team's formation,
        not where the return actually ended -- trusting them as a real state
        produced a WP swing attributed to the wrong team for a good return."""
        self.assertFalse(_valid_state(self._row(play_type="Kickoff")))

    def test_a_kickoff_return_touchdown_is_also_excluded(self):
        self.assertFalse(_valid_state(self._row(play_type="Kickoff Return Touchdown")))

    def test_a_punt_and_its_return_are_excluded_the_same_way(self):
        self.assertFalse(_valid_state(self._row(play_type="Punt", down=4)))
        self.assertFalse(_valid_state(self._row(play_type="Punt Return", down=4)))

    def test_a_missed_field_goal_return_is_excluded(self):
        self.assertFalse(_valid_state(self._row(play_type="Missed Field Goal Return", down=4)))

    def test_an_ordinary_scrimmage_down_is_still_valid(self):
        """The exclusion is scoped to the kick-family play types, not to every
        row that happens to reuse a 1st & 10 shape."""
        self.assertTrue(_valid_state(self._row(play_type="Rush")))
        self.assertTrue(_valid_state(self._row(play_type="Pass Reception")))

    def test_a_field_goal_attempt_is_still_valid(self):
        """Unlike a kickoff or punt, a field-goal attempt's down/distance is
        the real situation the offense faced, so it stays a usable anchor."""
        self.assertTrue(_valid_state(self._row(play_type="Field Goal Good", down=4)))


if __name__ == "__main__":
    unittest.main()
