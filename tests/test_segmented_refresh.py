from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sports_aggregator.tracked_refresh import _segment_for_light


EASTERN = ZoneInfo("America/New_York")


class SegmentedRefreshScheduleTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "CFB_REFRESH_TIMEZONE": "America/New_York",
        "CFB_REFRESH_CORE_HOURS": "6,18",
        "CFB_REFRESH_CONTENT_HOURS": "10,16",
        "CFB_REFRESH_ROSTER_HOURS": "12",
        "CFB_REFRESH_STATS_HOURS": "22",
        "CFB_REFRESH_MODEL_HOURS": "23",
    }, clear=False)
    def test_each_light_hour_routes_to_one_small_segment(self):
        expected = {
            6: "core",
            10: "content",
            12: "rosters",
            16: "content",
            18: "core",
            22: "stats",
            23: "models",
        }
        for hour, segment in expected.items():
            with self.subTest(hour=hour):
                moment = datetime(2026, 8, 27, hour, 0, tzinfo=EASTERN)
                self.assertEqual(_segment_for_light(moment), segment)

    def test_unscheduled_manual_light_defaults_to_core(self):
        moment = datetime(2026, 8, 27, 15, 0, tzinfo=EASTERN)
        with patch.dict(os.environ, {
            "CFB_REFRESH_CORE_HOURS": "6,18",
            "CFB_REFRESH_CONTENT_HOURS": "10,16",
            "CFB_REFRESH_ROSTER_HOURS": "12",
            "CFB_REFRESH_STATS_HOURS": "22",
            "CFB_REFRESH_MODEL_HOURS": "23",
        }, clear=False):
            self.assertEqual(_segment_for_light(moment), "core")


if __name__ == "__main__":
    unittest.main()
