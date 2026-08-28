from sports_aggregator.cfb.rivalries import annotate_game, rivalry_for, rivalries_for_team


def test_named_rivalries_resolve_by_school_pair():
    assert rivalry_for("Michigan", "Ohio State")["name"] == "The Game"
    assert rivalry_for("Auburn", "Alabama")["name"] == "Iron Bowl"
    assert rivalry_for("Washington State", "Washington")["name"] == "Apple Cup"


def test_team_aliases_resolve_common_cfbd_names():
    assert rivalry_for("Miami (OH)", "Cincinnati")["name"] == "Battle for the Bell"
    assert rivalry_for("Miami", "Florida State")["name"] == "Florida State–Miami"
    assert rivalry_for("NC State", "Clemson")["name"] == "Textile Bowl"


def test_non_rivalry_pair_is_not_marked():
    assert rivalry_for("Michigan", "Alabama") is None


def test_game_annotation_keeps_named_context():
    game = {"away_team": "Michigan", "home_team": "Ohio State"}
    annotated = annotate_game(game)
    assert annotated["rivalry_name"] == "The Game"
    assert annotated["rivalry"]["first_year"] == 1897


def test_team_rivalry_list_is_deterministic():
    names = [row["name"] for row in rivalries_for_team("Michigan")]
    assert "The Game" in names
    assert "Michigan–Michigan State" in names
    assert names == sorted(names)
