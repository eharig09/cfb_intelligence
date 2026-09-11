import unittest

from app import create_app


class VisualLabTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
        }).test_client()

    def test_visual_lab_renders_all_three_prototype_systems(self):
        response = self.client.get("/college-football/visual-lab/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for heading in ("Matchup edge board", "Season journey",
                        "Position percentile profile"):
            self.assertIn(heading, body)

    def test_mock_values_are_labelled_as_illustrative(self):
        body = self.client.get("/college-football/visual-lab/").get_data(as_text=True)
        self.assertIn("illustrative data", body)
        self.assertIn("none of the values below are live", body)

    def test_requested_second_round_visuals_are_present(self):
        body = self.client.get("/college-football/visual-lab/").get_data(as_text=True)
        for text in ("Texas with ball", "Michigan with ball", "Passing field matchup",
                     "Projected game shape", "Visual two-deep", "Upcoming matchup"):
            self.assertIn(text, body)
        self.assertNotIn("comfortable home win", body.lower())

    def test_trends_and_passing_zones_have_interactive_detail(self):
        body = self.client.get("/college-football/visual-lab/").get_data(as_text=True)
        self.assertGreaterEqual(body.count("data-team-trend"), 3)
        self.assertGreaterEqual(body.count("data-player-trend"), 3)
        self.assertIn("Receivers in zone", body)
        self.assertIn("rec ·", body)
        self.assertIn("yds ·", body)
        self.assertIn("TD", body)


if __name__ == "__main__":
    unittest.main()
