"""When a resolution pass should fail the refresh, and when it should not.

One dead seed handle marked eighteen of twenty-three refreshes degraded. The
first attempt to stop that divided failures by the number of handles attempted,
which cannot work: the resolver is handed exactly the handles that are not yet
verified, so that denominator is almost all failures by construction.
"""

from __future__ import annotations

import pytest

from sports_aggregator.social.cli import _endpoint_exit
from sports_aggregator.social.models import IdentityResolution


def _verified(handle: str) -> IdentityResolution:
    return IdentityResolution(handle, "did:plc:x", handle, handle, "verified")


def _gone(handle: str) -> IdentityResolution:
    """What Bluesky answers for a handle that is not a handle: 400."""
    return IdentityResolution(handle, None, None, None, "resolution_failed",
                              "400 Client Error: Bad Request", permanent=True)


def _unreachable(handle: str) -> IdentityResolution:
    return IdentityResolution(handle, None, None, None, "resolution_failed",
                              "ConnectionError: [Errno 111] Connection refused")


def test_a_single_dead_handle_does_not_fail_the_step(capsys):
    """The exact shape of the live failure: the retry list holds one handle,
    it has never resolved, and it will never resolve."""
    assert _endpoint_exit([_gone("skhanjr.bsky.social")], kind="bluesky") == 0
    out = capsys.readouterr().out
    assert "no longer exist" in out
    assert "skhanjr.bsky.social" in out


def test_every_dead_handle_together_still_does_not_fail_the_step():
    """The retry list is all-failures by construction, so 100% of it being
    dead handles is the steady state of a healthy registry, not an outage."""
    results = [_gone(f"gone-{n}.bsky.social") for n in range(6)]
    assert _endpoint_exit(results, kind="bluesky") == 0


def test_one_unreachable_handle_does_fail_the_step():
    """A connection error says nothing about whether the account exists, so it
    is not something to shrug at even when it is the only handle checked."""
    assert _endpoint_exit([_unreachable("slmandel.bsky.social")], kind="bluesky") == 1


def test_a_few_unreachable_among_many_verified_stay_within_tolerance():
    results = [_verified(f"ok-{n}.bsky.social") for n in range(19)]
    results.append(_unreachable("flaky.bsky.social"))
    assert _endpoint_exit(results, kind="bluesky") == 0


def test_widespread_unreachability_fails_even_beside_dead_handles():
    results = [_gone("gone.bsky.social")]
    results += [_unreachable(f"down-{n}.bsky.social") for n in range(5)]
    results += [_verified(f"ok-{n}.bsky.social") for n in range(4)]
    assert _endpoint_exit(results, kind="bluesky") == 1


def test_nothing_to_resolve_is_not_a_failure(capsys):
    assert _endpoint_exit([], kind="bluesky") == 0
    assert "nothing to resolve" in capsys.readouterr().out


def test_an_all_verified_pass_says_so(capsys):
    results = [_verified(f"ok-{n}.bsky.social") for n in range(3)]
    assert _endpoint_exit(results, kind="reddit") == 0
    assert "3/3 verified" in capsys.readouterr().out


def test_a_client_without_the_flag_is_treated_as_unreachable():
    """Reddit, podcasts and YouTube return a different result type that has no
    `permanent` field. Absent evidence that a handle is gone, a failure is the
    kind worth failing over -- the cautious reading, not the convenient one.
    """
    class Legacy:
        status = "resolution_failed"
        requested_handle = "someone"

    assert _endpoint_exit([Legacy()], kind="reddit") == 1


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self._response = response

    def get(self, *args, **kwargs):
        return self._response


def test_the_client_marks_a_4xx_as_permanent():
    """400 is Bluesky saying the handle is not a handle."""
    from sports_aggregator.social.bluesky import BlueskyIdentityClient
    client = BlueskyIdentityClient(session=_Session(_Response(400)))

    result = client.resolve("skhanjr.bsky.social")

    assert result.status == "resolution_failed"
    assert result.permanent is True


def test_the_client_does_not_mark_a_5xx_as_permanent():
    """A server error says nothing about whether the account exists."""
    from sports_aggregator.social.bluesky import BlueskyIdentityClient
    client = BlueskyIdentityClient(session=_Session(_Response(503)))

    result = client.resolve("slmandel.bsky.social")

    assert result.status == "resolution_failed"
    assert result.permanent is False


def test_a_transport_error_is_not_permanent():
    from sports_aggregator.social.bluesky import BlueskyIdentityClient

    class Broken:
        def get(self, *args, **kwargs):
            raise OSError("connection refused")

    result = BlueskyIdentityClient(session=Broken()).resolve("slmandel.bsky.social")

    assert result.status == "resolution_failed"
    assert result.permanent is False
