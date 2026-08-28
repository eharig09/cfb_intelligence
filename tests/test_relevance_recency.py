from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from sports_aggregator.social.relevance import recency_factor


class RelevanceRecencyTests(unittest.TestCase):
    def test_same_day_content_gets_freshness_boost(self):
        now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        published = (now - timedelta(hours=2)).isoformat()
        self.assertGreater(recency_factor(published, 7.0, now), 1.0)

    def test_nominal_half_life_decays_below_old_fifty_percent_curve(self):
        now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        published = (now - timedelta(days=7)).isoformat()
        factor = recency_factor(published, 7.0, now)
        self.assertLess(factor, 0.4)
        self.assertGreater(factor, 0.25)

    def test_older_content_degrades_quickly(self):
        now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        fresh = recency_factor((now - timedelta(hours=12)).isoformat(), 7.0, now)
        week_old = recency_factor((now - timedelta(days=7)).isoformat(), 7.0, now)
        two_weeks_old = recency_factor((now - timedelta(days=14)).isoformat(), 7.0, now)
        self.assertGreater(fresh, week_old)
        self.assertGreater(week_old, two_weeks_old)
        self.assertLess(two_weeks_old, 0.15)

    def test_undated_content_is_penalized(self):
        self.assertEqual(recency_factor(None, 7.0), 0.20)


if __name__ == "__main__":
    unittest.main()
