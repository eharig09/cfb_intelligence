"""Every failure the pipeline can record classifies, and every class has a fix.

The status page's whole promise is that a degraded refresh comes with a next
step. That holds only if `classify` never shrugs on a real message and `remedy`
always returns an action.
"""

from __future__ import annotations

import pytest

from sports_aggregator.refresh_remediation import (
    _GENERIC, STEP_SEGMENT, classify, remedy, self_heals,
)


@pytest.mark.parametrize("status,message,expected", [
    ("skipped", "needs YOUTUBE_API_KEY", "not_configured"),
    ("skipped", "needs REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET", "not_configured"),
    ("failed", "needs CFBD_API_KEY", "not_configured"),
    ("timeout", "exceeded 1800s", "timeout"),
    ("failed", "exceeded 900s and was stopped", "timeout"),
    ("failed", "Daily API request limit exceeded. Please try again tomorrow.", "upstream_quota"),
    ("failed", "open-meteo daily quota exhausted", "upstream_quota"),
    ("failed", "RuntimeError: can't start new thread", "resource"),
    ("failed", "MemoryError", "resource"),
    ("failed", "0 rows across 10 conferences", "empty_result"),
    ("failed", "endpoints=18 stored 0 errors=18", "empty_result"),
    ("failed", "HTTP 503 from provider", "upstream_error"),
    ("failed", "ConnectionError: connection reset by peer", "upstream_error"),
    ("failed", "something nobody has seen before", "unknown"),
])
def test_classify_names_the_kind(status, message, expected):
    assert classify(status=status, message=message) == expected


def test_only_a_spent_quota_is_treated_as_self_healing():
    assert self_heals("upstream_quota") is True
    for category in _GENERIC:
        if category != "upstream_quota":
            assert self_heals(category) is False


def test_every_category_has_a_generic_remedy_with_an_action():
    for category in _GENERIC:
        fix = remedy(step="whatever", category=category, segment="content")
        assert fix["cause"] and fix["action"]
        assert "{segment}" not in fix["action"]
        assert "{segment}" not in fix["cause"]


def test_a_quota_failure_points_at_no_rerun_because_none_would_help():
    fix = remedy(step="weather", category="upstream_quota", segment="models")
    assert fix["rerun_segment"] is None
    assert fix["self_heals"] is True
    assert "00:00 UTC" in fix["action"]


def test_a_timeout_points_at_its_own_segment_to_rerun():
    fix = remedy(step="pbp", category="timeout", segment="analytics")
    assert fix["rerun_segment"] == "analytics"


def test_a_missing_key_names_the_segment_to_rerun_after_setting_it():
    fix = remedy(step="youtube", category="not_configured", segment="content")
    assert fix["rerun_segment"] == "content"
    assert "YOUTUBE_API_KEY" in fix["cause"]


def test_the_owning_segment_is_used_when_the_caller_does_not_pass_one():
    fix = remedy(step="pbp", category="timeout")
    assert fix["rerun_segment"] == "analytics"  # from STEP_SEGMENT


def test_an_unrecognised_category_falls_back_to_unknown_not_a_crash():
    fix = remedy(step="pbp", category="not-a-real-category", segment="analytics")
    assert fix["category"] == "unknown"
    assert fix["action"]


def test_the_step_segment_map_only_names_real_segments():
    from sports_aggregator.tracked_refresh import SEGMENTS
    assert set(STEP_SEGMENT.values()) <= set(SEGMENTS)
