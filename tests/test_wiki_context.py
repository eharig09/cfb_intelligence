from sports_aggregator.cfb.wiki_context import _first, _first_season, _infobox_fields


def test_college_football_infobox_identity_fields():
    text = """
{{Infobox college football team
| teamname = Example State
| firstyear = 1898
| natltitles = 2 (1947, 1997)
| conftitles = 43
| mascotdisplay = [[Biff the Wolverine|Biff]]
| stadium = Example Stadium
}}
"""
    fields = _infobox_fields(text)
    assert _first_season(fields) == 1898
    assert _first(fields, "natltitles") == "2 (1947, 1997)"
    assert _first(fields, "conftitles") == "43"
    assert _first(fields, "mascotdisplay") == "Biff"


def test_missing_infobox_is_safe():
    fields = _infobox_fields("No football infobox here")
    assert fields == {}
    assert _first_season(fields) is None
