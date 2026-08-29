from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sports_aggregator.cfb.dataset_cli import sync_dataset


class LiveScoreRefreshTests(unittest.TestCase):
    @patch("sports_aggregator.cfb.dataset_cli.CFBDClient")
    @patch("sports_aggregator.cfb.dataset_cli.CFBRepository")
    def test_games_dataset_always_fetches_fresh_cfbd_games(self, repository_cls, client_cls):
        repository = repository_cls.return_value
        repository.replace_games.return_value = 888
        client = client_cls.return_value
        client.configured = True
        client.games.return_value = []

        count = sync_dataset("games", 2026)

        self.assertEqual(count, 888)
        client.games.assert_called_once_with(2026, True)
        repository.replace_games.assert_called_once()


if __name__ == "__main__":
    unittest.main()
