import csv
import os
from pathlib import Path
import tempfile
import unittest

from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.pff import DATASETS, PFFImporter, pff_summary
from sports_aggregator.cfb.repository import CFBRepository


class PFFImporterTests(unittest.TestCase):
    def test_import_preserves_provenance_and_links_exact_same_team_player(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = CFBRepository(root / "cfb.sqlite3")
            repository.replace_teams((Team(1, "Michigan", "Wolverines", "MICH", "Big Ten", None,
                                           "fbs", None, None, (), ("Michigan", "MICH"), None, None),))
            repository.replace_players(2026, (Player("cfbd-1", 2026, "Alex", "Example", "Michigan",
                                                     "QB", 7, 74, 215, 3),))
            pff_dir = root / "PFF"
            pff_dir.mkdir()
            for filename, (_, grade_field, usage_field) in DATASETS.items():
                fields = ["player", "player_id", "position", "team_name", "player_game_count",
                          grade_field, usage_field]
                with (pff_dir / filename).open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    writer.writerow({"player": "Alex Example", "player_id": "pff-1", "position": "QB",
                                     "team_name": "MICH", "player_game_count": "12",
                                     grade_field: "91.2", usage_field: "400"})

            report = PFFImporter(repository).import_directory(pff_dir, season=2025, roster_season=2026)
            self.assertEqual(report.files, 7)
            self.assertEqual(report.rows, 7)
            self.assertEqual(report.linked_players, 1)
            summary = pff_summary(repository, 2025)
            self.assertEqual(summary["players"], 1)
            self.assertEqual(summary["top_players"][0]["cfbd_player_id"], "cfbd-1")
            self.assertEqual(len(summary["top_position_groups"]), 0)


if __name__ == "__main__":
    unittest.main()
