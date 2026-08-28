from sports_aggregator.cfb.wiki_context_enrichment import (
    _compact_titles,
    _rendered_history,
    _rendered_infobox,
)


def test_compact_titles_counts_years_and_uses_latest():
    value = "OAC: 1906, 1912 Big Ten: 1916, 1917, 2020"
    assert _compact_titles(value) == "5 (2020)"


def test_rendered_infobox_keeps_unclaimed_national_titles_out_of_headline():
    html = """
    <table class="infobox">
      <tr><th>First season</th><td>1894</td></tr>
      <tr><th>Conference titles</th><td>1919, 1933, 2024</td></tr>
      <tr><th>Unclaimed national titles</th><td>2024</td></tr>
      <tr><th>Mascot</th><td>The Duck</td></tr>
    </table>
    """
    fields = _rendered_history(_rendered_infobox(html))
    assert fields["first_season"] == 1894
    assert fields["conference_championships"] == "1919, 1933, 2024"
    assert fields["national_championships"] is None
    assert fields["has_unclaimed"] is True


def test_rendered_infobox_accepts_claimed_national_title():
    html = """
    <table class="infobox">
      <tr><th>First year</th><td>1887</td></tr>
      <tr><th>Claimed national titles</th><td>2025</td></tr>
      <tr><th>Conference championships</th><td>1945, 1967, 2025</td></tr>
    </table>
    """
    fields = _rendered_history(_rendered_infobox(html))
    assert fields["first_season"] == 1887
    assert fields["national_championships"] == "2025"
    assert fields["conference_championships"] == "1945, 1967, 2025"
