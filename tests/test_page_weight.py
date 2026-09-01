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

    def test_the_report_template_carries_no_stylesheet(self):
        from pathlib import Path
        source = Path("templates/cfb_box_score.html").read_text(encoding="utf-8")
        self.assertNotIn("<style>", source)

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
