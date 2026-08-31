import gzip
import unittest

from app import create_app
from sports_aggregator.compression import MIN_BYTES


LARGE = "<p>college football</p>" * 400


def build():
    app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False})

    @app.get("/_test/large")
    def large():
        return LARGE

    @app.get("/_test/small")
    def small():
        return "<p>ok</p>"

    @app.get("/_test/binary")
    def binary():
        return app.response_class(b"\x00\x01" * 4000, mimetype="image/png")

    @app.get("/_test/already-encoded")
    def already_encoded():
        response = app.response_class(gzip.compress(LARGE.encode()), mimetype="text/html")
        response.headers["Content-Encoding"] = "gzip"
        return response

    return app


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.client = build().test_client()

    def get(self, path, encoding="gzip"):
        return self.client.get(path, headers={"Accept-Encoding": encoding})

    def test_a_large_text_response_is_compressed(self):
        response = self.get("/_test/large")
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertLess(len(response.get_data()), len(LARGE))

    def test_the_compressed_body_is_the_body_that_was_asked_for(self):
        compressed = self.get("/_test/large").get_data()
        plain = self.get("/_test/large", encoding="identity").get_data()
        self.assertEqual(gzip.decompress(compressed), plain)

    def test_content_length_describes_the_bytes_actually_sent(self):
        response = self.get("/_test/large")
        self.assertEqual(int(response.headers["Content-Length"]),
                         len(response.get_data()))

    def test_a_client_that_does_not_ask_for_gzip_does_not_get_it(self):
        response = self.get("/_test/large", encoding="identity")
        self.assertIsNone(response.headers.get("Content-Encoding"))

    def test_a_compressible_response_always_varies_on_the_request_header(self):
        """Otherwise a shared cache serves a gzipped body to a client that cannot read it."""
        for encoding in ("gzip", "identity"):
            response = self.get("/_test/large", encoding=encoding)
            self.assertIn("Accept-Encoding", response.headers.get("Vary", ""))

    def test_a_body_too_small_to_benefit_is_left_alone(self):
        response = self.get("/_test/small")
        self.assertLess(len(response.get_data()), MIN_BYTES)
        self.assertIsNone(response.headers.get("Content-Encoding"))

    def test_already_compressed_formats_are_not_compressed_again(self):
        response = self.get("/_test/binary")
        self.assertIsNone(response.headers.get("Content-Encoding"))

    def test_a_response_that_encoded_itself_is_not_double_encoded(self):
        response = self.get("/_test/already-encoded")
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(response.get_data()).decode(), LARGE)

    def test_static_files_are_compressed_too(self):
        """The stylesheet is the largest single asset on a cold visit."""
        response = self.get("/static/cfb.css")
        plain = self.get("/static/cfb.css", encoding="identity")
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(response.get_data()), plain.get_data())
        self.assertLess(len(response.get_data()), len(plain.get_data()) // 2)

    def test_a_compressed_static_file_no_longer_claims_a_strong_validator(self):
        """The bytes sent are not the bytes the strong etag was computed over."""
        response = self.get("/static/cfb.css")
        self.assertTrue(response.headers["ETag"].startswith("W/"))

    def test_revalidation_still_works_through_the_hook(self):
        first = self.get("/static/cfb.css")
        again = self.client.get("/static/cfb.css", headers={
            "Accept-Encoding": "gzip", "If-None-Match": first.headers["ETag"]})
        self.assertEqual(again.status_code, 304)

    def test_a_head_request_reports_what_a_get_would_send(self):
        head = self.client.head("/_test/large", headers={"Accept-Encoding": "gzip"})
        get = self.get("/_test/large")
        self.assertEqual(head.headers["Content-Encoding"], "gzip")
        self.assertEqual(head.headers["Content-Length"], get.headers["Content-Length"])
        self.assertEqual(head.get_data(), b"")


if __name__ == "__main__":
    unittest.main()
