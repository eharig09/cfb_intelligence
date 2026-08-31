import re
import shutil
import tempfile
import unittest
from pathlib import Path

from app import create_app
from sports_aggregator.client_cache import IMMUTABLE_SECONDS, file_stamp


class StaticStampTests(unittest.TestCase):
    """A URL may only promise to be immutable if it names the bytes it serves."""

    def setUp(self):
        self.app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False})
        self.static = Path(self.app.static_folder)

    def url(self, filename="cfb.css"):
        from flask import url_for
        with self.app.test_request_context("/"):
            return url_for("static", filename=filename)

    def test_every_static_url_carries_a_version(self):
        for filename in ("cfb.css", "cfb_tables.js", "icon.svg", "manifest.webmanifest"):
            self.assertRegex(self.url(filename), r"\?v=[0-9a-f]+$",
                             f"{filename} went out unversioned")

    def test_the_version_follows_the_contents(self):
        original = (self.static / "cfb.css").read_bytes()
        before = self.url()
        try:
            (self.static / "cfb.css").write_bytes(original + b"\n/* edit */\n")
            self.assertNotEqual(self.url(), before)
        finally:
            (self.static / "cfb.css").write_bytes(original)
        self.assertEqual(self.url(), before)

    def test_rewriting_the_same_bytes_does_not_change_the_version(self):
        """A deploy rewrites every mtime; that must not expire every asset."""
        path = self.static / "cfb.css"
        original = path.read_bytes()
        before = self.url()
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "cfb.css"
            shutil.copyfile(path, copy)
            path.unlink()
            shutil.copyfile(copy, path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.url(), before)

    def test_a_missing_file_gets_no_version(self):
        self.assertIsNone(file_stamp(self.static / "not-a-real-file.css"))
        self.assertNotIn("?v=", self.url("not-a-real-file.css"))


class CacheHeaderTests(unittest.TestCase):
    def setUp(self):
        # TESTING disables the page cache but must not disable the headers:
        # `cached_page` still marks the request public before it checks.
        self.app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False})
        self.client = self.app.test_client()

    def versioned(self, filename):
        from flask import url_for
        with self.app.test_request_context("/"):
            return url_for("static", filename=filename)

    def test_a_versioned_asset_is_cached_forever(self):
        response = self.client.get(self.versioned("cfb.css"))
        self.assertEqual(response.headers["Cache-Control"],
                         f"public, max-age={IMMUTABLE_SECONDS}, immutable")

    def test_an_unversioned_asset_is_not(self):
        """Reachable by hand-typed URL, and it is not a promise we can keep."""
        response = self.client.get("/static/cfb.css")
        self.assertNotIn("immutable", response.headers.get("Cache-Control", ""))

    def test_a_public_page_is_briefly_cacheable(self):
        response = self.client.get("/college-football/scoreboard/")
        self.assertEqual(response.status_code, 200)
        directives = response.headers.get("Cache-Control", "")
        self.assertIn("public", directives)
        self.assertRegex(directives, r"max-age=\d+")

    def test_a_public_page_is_not_cached_long_enough_to_show_a_stale_score(self):
        response = self.client.get("/college-football/scoreboard/")
        seconds = int(re.search(r"max-age=(\d+)", response.headers["Cache-Control"]).group(1))
        self.assertLessEqual(seconds, 300)

    def test_a_page_that_is_not_public_gets_no_cache_control(self):
        """Only `cached_page` declares a page identical for every reader."""
        response = self.client.get("/college-football/data-status/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("Cache-Control"))

    def test_an_error_is_never_cached(self):
        response = self.client.get("/college-football/games/999999999/")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIsNone(response.headers.get("Cache-Control"))


if __name__ == "__main__":
    unittest.main()
