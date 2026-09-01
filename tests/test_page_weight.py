import re
import unittest

from app import create_app


ANALYSIS_MODULES = (
    "postgame_display", "postgame_analytics_display", "postgame_tendencies_display",
    "qb_air_yards_display", "passing_display",
)


class InlineStyleTests(unittest.TestCase):
    """Rules that never change belong in a file the browser keeps.

    Each analysis module used to ship its own <style> with the markup it
    returned, so a postgame report carried about 25 KB of identical CSS on every
    render and repeated one module's block twice, because two of its blocks
    appear on the same page.
    """

    def test_the_display_modules_emit_no_stylesheet_of_their_own(self):
        import importlib
        for name in ANALYSIS_MODULES:
            module = importlib.import_module(f"sports_aggregator.cfb.{name}")
            self.assertEqual(getattr(module, "STYLE", ""), "",
                             f"{name} is inlining CSS again")

    def test_no_page_template_carries_a_stylesheet(self):
        """Each page's rules live in static/pages/, fetched once and cached.

        They are separate files rather than one bundle on purpose: nine
        selectors are defined differently by different pages -- `.hero` means
        four things, `.logo` is display:none on one page and 92px on another --
        and merging them would let the last one win everywhere.
        """
        from pathlib import Path
        for template in sorted(Path("templates").glob("cfb_*.html")):
            source = template.read_text(encoding="utf-8")
            if 'extends "_layout.html"' not in source:
                continue
            self.assertNotIn("<style>", source, f"{template.name} inlines CSS")

    def test_every_page_stylesheet_exists_for_the_template_that_links_it(self):
        import re
        from pathlib import Path
        for template in sorted(Path("templates").glob("cfb_*.html")):
            source = template.read_text(encoding="utf-8")
            for name in re.findall(r"filename='pages/([a-z_]+\.css)'", source):
                self.assertTrue((Path("static/pages") / name).exists(),
                                f"{template.name} links a missing {name}")

    def test_the_layout_keeps_only_the_rule_that_must_be_inline(self):
        """The <noscript> block cannot move: it applies only without scripting."""
        import re
        from pathlib import Path
        source = Path("templates/_layout.html").read_text(encoding="utf-8")
        blocks = re.findall(r"<style[^>]*>(.*?)</style>", source, re.S)
        self.assertEqual(len(blocks), 1)
        self.assertIn(".tabpanel[hidden]", blocks[0])

    def test_the_shared_stylesheet_covers_what_the_modules_use(self):
        from pathlib import Path
        css = Path("static/cfb_analysis.css").read_text(encoding="utf-8")
        for cls in ("postgame-shell", "pg-turn", "pg-tendency", "pg-qb-air",
                    "mof-grid", "mof-phases", "box-report", "report-cover",
                    "pass-qb-facts", "efficiency-table"):
            self.assertIn("." + cls, css, f"{cls} lost its rules")

    def test_the_print_stylesheet_survived_the_move(self):
        from pathlib import Path
        css = Path("static/cfb_analysis.css").read_text(encoding="utf-8")
        self.assertIn("@media print", css)
        # A closed disclosure and a clamped table both have to open for print.
        self.assertIn("max-height:none!important", css.replace(" ", ""))


class InlineAttributeTests(unittest.TestCase):
    """Presentation in a style attribute cannot be themed or overridden.

    The shared shell is the one place where that matters most, because whatever
    it carries appears on every page. Attributes the server computes are fine --
    team identity genuinely varies per request -- so only static ones are barred.
    """

    def test_the_shared_layout_carries_no_static_style_attributes(self):
        from pathlib import Path
        source = Path("templates/_layout.html").read_text(encoding="utf-8")
        static = [value for value in re.findall(r'style="([^"]{25,})"', source)
                  if "{{" not in value and "{%" not in value]
        self.assertEqual(static, [], "move these into a class")


class ReportWeightTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app({
            "TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False}).test_client()

    def test_pages_link_the_shared_analysis_stylesheet(self):
        body = self.client.get("/college-football/scoreboard/").get_data(as_text=True)
        self.assertRegex(body, r"cfb_analysis\.css\?v=[0-9a-f]+")

    def test_no_analysis_rules_are_inlined_anywhere(self):
        """The per-page templates still carry their own CSS; the analysis blocks
        must not. Those are the ones that were duplicated within a single render
        and shipped on every request.
        """
        for path in ("/college-football/scoreboard/", "/college-football/"):
            body = self.client.get(path).get_data(as_text=True)
            inline = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", body, re.S))
            for selector in (".postgame-shell", ".pg-turn", ".mof-grid", ".box-report"):
                self.assertNotIn(selector, inline,
                                 f"{selector} is inlined again on {path}")


if __name__ == "__main__":
    unittest.main()
