import os
import tempfile
import unittest

from sports_aggregator.social.bluesky import BlueskyIdentityClient
from sports_aggregator.social.models import IdentityResolution, SourceProfile
from sports_aggregator.social.registry import SourceRegistry


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class IdentitySession:
    def get(self, url, **kwargs):
        if "resolveHandle" in url:
            return FakeResponse({"did": "did:plc:stable"})
        return FakeResponse({"did": "did:plc:stable", "handle": "reporter.example",
                             "displayName": "Reporter", "description": "CFB reporter"})


class SocialRegistryTests(unittest.TestCase):
    def test_did_and_current_handle_must_both_verify(self):
        result = BlueskyIdentityClient(session=IdentitySession()).resolve("reporter.example")
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.did, "did:plc:stable")

    def test_seed_metadata_and_resolution_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = SourceRegistry(os.path.join(directory, "cfb.sqlite3"))
            profile = SourceProfile("reporter.example", "Reporter", "Outlet", "REPORTER",
                                    ("breaking_news",), teams=("Michigan",), reliability=5)
            self.assertEqual(registry.seed((profile,)), 1)
            registry.store_resolution(
                BlueskyIdentityClient(session=IdentitySession()).resolve(profile.handle)
            )
            status = registry.status()
            self.assertEqual(status["verified"], 1)
            self.assertEqual(status["sources"][0]["teams"], ["Michigan"])
            registry.store_resolution(IdentityResolution(
                profile.handle, None, None, None, "resolution_failed", "temporary error"
            ))
            failed = registry.status()["sources"][0]
            self.assertEqual(failed["did"], "did:plc:stable")
            self.assertEqual(failed["last_error"], "temporary error")


if __name__ == "__main__":
    unittest.main()
