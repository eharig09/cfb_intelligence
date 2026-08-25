"""The dark theme, and the contrast guarantees that make it usable.

The light page was the only page for a long time, so a great deal of the
stylesheet assumed it. These tests hold the two invariants that assumption left
behind: every color is a token that can follow the theme, and every team accent
is legible against whichever background it will actually be painted on.
"""

from __future__ import annotations

import glob
import os
import re
import tempfile
import unittest

from app import create_app
from sports_aggregator.cfb import identity as I
from sports_aggregator.cfb.models import Player, Team
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.social.content import ContentRepository


STYLESHEET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "static", "cfb.css")


def _read_css() -> str:
    with open(STYLESHEET, encoding="utf-8") as handle:
        return handle.read()


def _blocks(css: str, selector: str) -> list[str]:
    """Every declaration body for `selector`, matched by brace depth."""
    found = []
    for match in re.finditer(re.escape(selector) + r"\s*\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        found.append(css[match.end():index - 1])
    return found


def _declarations(block: str) -> dict[str, str]:
    return {name.strip(): value.strip() for name, value in
            re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


class TokenDisciplineTests(unittest.TestCase):
    """A literal color outside the token blocks cannot follow the theme."""

    def setUp(self):
        self.css = _read_css()

    def test_no_literal_colors_outside_the_token_blocks(self):
        stripped = self.css
        # Remove every custom-property declaration; those are the token blocks
        # and are the only place a literal belongs.
        stripped = re.sub(r"--[\w-]+\s*:\s*[^;]+;", "", stripped)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
        leftovers = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
        self.assertEqual(
            sorted(set(leftovers)), [],
            "hardcoded colors cannot follow the theme; move them into tokens")

    def test_templates_carry_no_colors_the_theme_cannot_reach(self):
        """Page-local <style> blocks are as binding as the stylesheet.

        An earlier scan for these used a single-line pattern and silently missed
        every multi-line rule, which is how the VS puck and the rank badges
        shipped inverted.
        """
        leaks = []
        for path in sorted(glob.glob(os.path.join(
                os.path.dirname(STYLESHEET), "..", "templates", "*.html"))):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            # Only pages on the shared shell inherit the theme; the standalone
            # admin views carry their own self-contained palette.
            if 'extends "_layout.html"' not in text:
                continue
            for style in re.findall(r"<style>(.*?)</style>", text, re.S):
                for declaration in re.findall(r"[\w.#\[\]:>\-,\s]+\{[^}]*\}", style):
                    body = declaration.split("{", 1)[1].rstrip("}")
                    for property_ in body.split(";"):
                        if re.search(r"#[0-9a-fA-F]{3,8}\b", property_):
                            leaks.append(f"{os.path.basename(path)}: {property_.strip()}")
                        # --ink is the body text color. Painting a background
                        # with it inverts the element in dark mode.
                        if re.search(r"background\s*:\s*var\(--ink\)", property_):
                            leaks.append(f"{os.path.basename(path)}: {property_.strip()}"
                                         " (use --surface-strong for chrome)")
        self.assertEqual(leaks, [])

    def test_both_dark_entry_points_declare_the_same_tokens(self):
        """The media query and the attribute must not drift apart.

        One serves a reader who never touched the control and the other a reader
        who did. A token added to one and not the other is invisible until
        somebody happens to arrive by the other route.
        """
        by_media = _blocks(self.css, ':root:not([data-theme="light"])')
        by_attribute = _blocks(self.css, ':root[data-theme="dark"]')
        self.assertTrue(by_media and by_attribute)
        media_tokens = _declarations(by_media[0])
        attribute_tokens = _declarations(by_attribute[0])
        self.assertEqual(media_tokens, attribute_tokens)
        self.assertGreater(len(media_tokens), 30, "dark palette looks incomplete")

    def test_every_light_token_has_a_dark_counterpart(self):
        light = _declarations(_blocks(self.css, ":root")[0])
        dark = _declarations(_blocks(self.css, ':root[data-theme="dark"]')[0])
        # Fonts, spacing, and the identity pairs themselves do not change with
        # the theme; every color token must.
        theme_independent = {
            "--display-font", "--body-font", "--shell", "--gap", "--pad",
            "--team-light", "--team-dark", "--conference-light", "--conference-dark",
            "--team-fill", "--team-on-fill", "--rust", "--color-scheme",
            # School marks are drawn for white grounds, so the disc behind one
            # stays white on both themes. Constant by intent, not by omission.
            "--logo-ground",
        }
        missing = sorted(name for name in light
                         if name not in theme_independent and name not in dark)
        self.assertEqual(missing, [], "tokens with no dark value would stay light")

    def test_the_stylesheet_holds_no_control_characters(self):
        """A mangled CSS escape writes a raw control byte and fails silently.

        The rule still parses, so nothing errors: the marker simply renders as
        nothing. Cheap to assert, invisible to review.
        """
        allowed = {ord(chr(10)), ord(chr(13)), ord(chr(9))}
        found = sorted({hex(ord(char)) for char in self.css
                        if ord(char) < 32 and ord(char) not in allowed})
        self.assertEqual(found, [])

    def test_both_themes_declare_a_color_scheme(self):
        self.assertIn("color-scheme: light", self.css)
        self.assertIn("color-scheme: dark", self.css)

    def test_an_explicit_light_choice_survives_a_dark_system(self):
        """Without the :not() guard the media query would beat the toggle."""
        self.assertIn(':root:not([data-theme="light"])', self.css)


class ContrastTests(unittest.TestCase):
    """Accents must clear contrast on the background they are painted on."""

    #: Real brand colors that previously exercised the edges: near-white,
    #: near-black, pale gold, and the deep navies that a darken-only correction
    #: would have driven into the dark background.
    SAMPLES = {
        "Michigan navy": "#00274C",
        "Michigan maize": "#FFCB05",
        "Penn State navy": "#041E42",
        "Boise State blue": "#0033A0",
        "pure white": "#FFFFFF",
        "pure black": "#000000",
        "Oregon green": "#154733",
        "Syracuse orange": "#D44500",
        "Georgia Tech gold": "#B3A369",
    }

    def test_light_accents_are_legible_on_the_light_page(self):
        for name, color in self.SAMPLES.items():
            with self.subTest(team=name):
                accent = I.readable_accent(color)
                self.assertGreaterEqual(
                    I.contrast_ratio(I._channels(accent), I.PAGE_BACKGROUND), 3.0, name)

    def test_dark_accents_are_legible_on_the_dark_page(self):
        for name, color in self.SAMPLES.items():
            with self.subTest(team=name):
                accent = I.dark_accent(color)
                self.assertGreaterEqual(
                    I.contrast_ratio(I._channels(accent), I.DARK_PAGE_BACKGROUND), 3.0, name)

    def test_a_deep_navy_is_lightened_rather_than_darkened_for_the_dark_page(self):
        """The bug this whole change exists to prevent.

        A single light-page accent reused on the dark page darkens an already
        dark color, driving it into the background.
        """
        navy = "#00274C"
        light = I.readable_accent(navy)
        dark = I.dark_accent(navy)
        self.assertEqual(light.lower(), navy.lower(), "a legible navy should pass through")
        self.assertGreater(I._relative_luminance(I._channels(dark)),
                           I._relative_luminance(I._channels(light)))
        # Reusing the light value on the dark page is what must not happen.
        self.assertLess(
            I.contrast_ratio(I._channels(light), I.DARK_PAGE_BACKGROUND), 3.0,
            "sample no longer demonstrates the failure it guards against")

    def test_a_pale_color_is_darkened_for_the_light_page_and_left_alone_on_dark(self):
        maize = "#FFCB05"
        self.assertNotEqual(I.readable_accent(maize).lower(), maize.lower())
        self.assertEqual(I.dark_accent(maize).lower(), maize.lower())

    def test_an_already_legible_color_is_returned_unchanged(self):
        """Moving a color that does not need moving loses the school's identity."""
        self.assertEqual(I.readable_accent("#D44500").lower(), "#d44500")
        self.assertEqual(I.dark_accent("#D44500").lower(), "#d44500")

    def test_the_conference_palette_is_legible_in_both_themes(self):
        for conference in I.CONFERENCE_COLORS:
            with self.subTest(conference=conference):
                light = I.conference_color(conference)
                dark = I.conference_color_dark(conference)
                self.assertGreaterEqual(
                    I.contrast_ratio(I._channels(light), I.PAGE_BACKGROUND), 3.0)
                self.assertGreaterEqual(
                    I.contrast_ratio(I._channels(dark), I.DARK_PAGE_BACKGROUND), 3.0)

    def test_a_missing_or_invalid_color_still_yields_no_accent(self):
        for value in (None, "", "not-a-color", "#12"):
            self.assertIsNone(I.readable_accent(value))
            self.assertIsNone(I.dark_accent(value))

    def test_team_identity_carries_both_accents(self):
        identity = I.team_identity({"color": "#00274C", "conference": "Big Ten"})
        self.assertIn("accent", identity)
        self.assertIn("accent_dark", identity)
        self.assertNotEqual(identity["accent"], identity["accent_dark"])
        self.assertIn("conference_color_dark", identity)

    def test_a_team_without_a_color_falls_back_to_its_conference_in_both_themes(self):
        identity = I.team_identity({"conference": "SEC"})
        self.assertEqual(identity["accent"], I.DEFAULT_CONFERENCE_COLOR)
        self.assertGreaterEqual(
            I.contrast_ratio(I._channels(identity["accent_dark"]),
                             I.DARK_PAGE_BACKGROUND), 3.0)

    def test_the_fill_foreground_does_not_change_with_the_theme(self):
        """A fill covers the page, so only the fill's own luminance matters."""
        self.assertEqual(I.foreground_for("#00274C"), "#ffffff")
        self.assertEqual(I.foreground_for("#FFCB05"), "#0b1728")


class ServedThemeTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.repository = CFBRepository(self.path)
        self.repository.replace_teams([Team.from_cfbd({
            "id": 68, "school": "Boise State", "mascot": "Broncos", "abbreviation": "BSU",
            "alternateNames": [], "conference": "Mountain West", "classification": "fbs",
            "color": "#0033A0", "logos": []})])
        self.repository.replace_players(2026, (
            Player("p1", 2026, "Test", "Player", "Boise State", "QB", 1, 74, 200, 3),
        ))
        ContentRepository(self.path).initialize()
        self.app = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False,
            "CFB_REPOSITORY": self.repository, "CFB_DEFAULT_SEASON": 2026,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.path)

    def test_the_theme_resolves_before_the_stylesheet_loads(self):
        """Resolving later paints light and then snaps to dark."""
        body = self.client.get("/college-football/teams/68/").get_data(as_text=True)
        head = body.split("</head>", 1)[0]
        bootstrap = head.index("cfb-theme")
        stylesheet = head.index("cfb.css")
        self.assertLess(bootstrap, stylesheet,
                        "theme bootstrap must precede the stylesheet")

    def test_pages_declare_a_theme_color_for_each_scheme(self):
        head = self.client.get("/college-football/").get_data(as_text=True)
        self.assertIn('name="theme-color" content="#f2f5f9"', head)
        self.assertIn('name="theme-color" content="#10151c"', head)

    def test_the_toggle_is_present_and_labelled(self):
        body = self.client.get("/college-football/").get_data(as_text=True)
        self.assertIn("data-theme-toggle", body)
        self.assertIn('aria-label="Switch to dark theme"', body)
        self.assertIn('aria-pressed="false"', body)

    def test_the_toggle_is_hidden_without_scripting(self):
        """A control that cannot persist a choice should not be offered."""
        css = _read_css()
        self.assertIn(".theme-toggle { display: none; }", css)
        self.assertIn(":root.js .theme-toggle { display: inline-flex; }", css)

    def test_identity_is_emitted_as_a_pair_on_every_themed_page(self):
        for path in ("/college-football/teams/68/",
                     "/college-football/players/p1/",
                     "/college-football/teams/68/history/",
                     "/college-football/conferences/mountain-west/"):
            with self.subTest(path=path):
                body = self.client.get(path).get_data(as_text=True)
                self.assertRegex(body, r"--team-light:#[0-9a-f]{6}", path)
                self.assertRegex(body, r"--team-dark:#[0-9a-f]{6}", path)

    def test_no_page_sets_the_derived_team_variable_inline(self):
        """An inline --team cannot be overridden by a theme rule.

        Inline declarations beat every selector, so the server must send the
        pair and let the stylesheet choose between them.
        """
        for path in ("/college-football/teams/68/",
                     "/college-football/players/p1/",
                     "/college-football/conferences/mountain-west/"):
            with self.subTest(path=path):
                body = self.client.get(path).get_data(as_text=True)
                self.assertNotRegex(body, r"style=\"[^\"]*--team:")
                self.assertNotRegex(body, r"style=\"[^\"]*--conference:")


if __name__ == "__main__":
    unittest.main()


class TeamMarkTests(unittest.TestCase):
    """Schools publish a dark-background mark; it must actually reach the page."""

    def test_the_pair_helper_separates_the_two_variants(self):
        from sports_aggregator.cfb.repository import _logo_pair
        logos = ["https://cdn.test/logos/500/2.png",
                 "https://cdn.test/logos-dark/500/2.png",
                 "https://cdn.test/logos/256/2.png",
                 "https://cdn.test/logos-dark/256/2.png"]
        light, dark = _logo_pair(logos)
        self.assertEqual(light, "https://cdn.test/logos/500/2.png")
        self.assertEqual(dark, "https://cdn.test/logos-dark/500/2.png")

    def test_a_school_with_one_mark_uses_it_on_both_themes(self):
        from sports_aggregator.cfb.repository import _logo_pair
        self.assertEqual(_logo_pair(["https://cdn.test/logos/500/2.png"]),
                         ("https://cdn.test/logos/500/2.png",
                          "https://cdn.test/logos/500/2.png"))
        self.assertEqual(_logo_pair([]), (None, None))

    def test_a_dark_only_list_still_yields_a_light_mark(self):
        from sports_aggregator.cfb.repository import _logo_pair
        light, dark = _logo_pair(["https://cdn.test/logos-dark/500/2.png"])
        self.assertEqual(light, dark)
        self.assertTrue(light)

    def test_the_mark_swaps_on_theme_rather_than_only_on_the_media_query(self):
        """A <picture> element cannot see the manual toggle; CSS can."""
        css = _read_css()
        self.assertIn(':root[data-theme="dark"] .team-mark', css)
        self.assertIn("--mark-dark", css)
