from sports_aggregator.cfb.leader_scope import sanitize_team_leader_packet


def test_team_packet_drops_cross_team_player_and_keeps_transfer_origin():
    packet = {
        "season": 2025,
        "groups": {
            "passing": {
                "players": [
                    {
                        "player_id": "arch",
                        "player": "Arch Manning",
                        "team": "Texas",
                        "stats": {"YDS": 3163},
                    },
                    {
                        "player_id": "mj",
                        "player": "MJ Morris",
                        "team": "Coastal Carolina",
                        "origin": "Coastal Carolina",
                        "arrival": True,
                        "stats": {"YDS": 304},
                    },
                ]
            }
        },
    }

    result = sanitize_team_leader_packet(
        packet, team="Florida State", active_ids={"mj"}
    )

    players = result["groups"]["passing"]["players"]
    assert [player["player"] for player in players] == ["MJ Morris"]
    assert players[0]["team"] == "Florida State"
    assert players[0]["origin"] == "Coastal Carolina"
    assert players[0]["arrival"] is True
    assert result["team_scope"] == "Florida State"
