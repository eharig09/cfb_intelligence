"""The segment-health roll-up: the file that survives the next segment's run.

Each segment overwrites refresh_progress.json and appends one history row, so
"the latest run" is only ever the last segment. segment_health.json is the
per-segment roll-up the status page reads instead.
"""

from __future__ import annotations

from pathlib import Path

from sports_aggregator import refresh_health
from sports_aggregator.refresh_health import (
    attention_items, classify_step_rows, not_configured_items, overall,
    read_health, summarize_segment,
)


def _report(profile, status, *, degraded=None, required=None, skipped=None,
            finished="2026-09-08T10:00:00+00:00"):
    return {
        "status": status, "profile": profile, "season": 2026,
        "finished_at": finished, "seconds": 12.0, "step_count": 3,
        "degraded_steps": degraded or [], "required_failures": required or [],
        "skipped_steps": skipped or [],
    }


def test_classify_step_rows_splits_three_ways():
    results = [
        {"step": "cfbd-sync", "status": "success", "optional": False},
        {"step": "cfbd-current-player-stats", "status": "failed",
         "message": "0 rows across 10 conferences", "optional": False},
        {"step": "weather", "status": "failed",
         "message": "Daily API request limit exceeded.", "optional": True},
        {"step": "youtube", "status": "skipped", "message": "needs YOUTUBE_API_KEY",
         "optional": True},
    ]
    required, degraded, skipped = classify_step_rows(results, segment="stats")

    assert [r["step"] for r in required] == ["cfbd-current-player-stats"]
    assert required[0]["category"] == "empty_result"
    assert [r["step"] for r in degraded] == ["weather"]
    assert degraded[0]["category"] == "upstream_quota"
    assert degraded[0]["self_heals"] is True
    assert [r["step"] for r in skipped] == ["youtube"]
    assert skipped[0]["category"] == "not_configured"


def test_a_degraded_segment_stays_visible_after_a_clean_one_runs(tmp_path: Path):
    summarize_segment(tmp_path, _report("content", "degraded", degraded=[
        {"step": "bluesky", "status": "failed", "message": "stored 0",
         "category": "empty_result", "self_heals": False, "segment": "content"}],
        finished="2026-09-08T10:00:00+00:00"))
    # An hour later a different segment runs clean.
    summarize_segment(tmp_path, _report("core", "success",
                                        finished="2026-09-08T11:00:00+00:00"))

    health = read_health(tmp_path)
    assert health["core"]["last_status"] == "success"
    assert health["content"]["last_status"] == "degraded"
    assert health["content"]["open_issues"][0]["step"] == "bluesky"

    items = attention_items(tmp_path)
    assert [i["step"] for i in items] == ["bluesky"]
    assert items[0]["action"]  # a remedy came through


def test_first_seen_survives_repeated_degrades(tmp_path: Path):
    row = {"step": "pbp", "status": "timeout", "message": "exceeded 1800s",
           "category": "timeout", "self_heals": False, "segment": "analytics"}
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[row],
                                        finished="2026-09-06T02:00:00+00:00"))
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[row],
                                        finished="2026-09-08T02:00:00+00:00"))

    entry = read_health(tmp_path)["analytics"]
    assert entry["consecutive_degraded"] == 2
    assert entry["open_issues"][0]["since"] == "2026-09-06T02:00:00+00:00"


def test_a_clean_run_clears_the_issues_and_the_streak(tmp_path: Path):
    row = {"step": "pbp", "status": "timeout", "message": "exceeded 1800s",
           "category": "timeout", "self_heals": False, "segment": "analytics"}
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[row]))
    summarize_segment(tmp_path, _report("analytics", "success",
                                        finished="2026-09-09T02:00:00+00:00"))

    entry = read_health(tmp_path)["analytics"]
    assert entry["open_issues"] == []
    assert entry["consecutive_degraded"] == 0
    assert entry["last_success_at"] == "2026-09-09T02:00:00+00:00"
    assert attention_items(tmp_path) == []


def test_attention_is_worst_first_then_actionable_before_self_healing(tmp_path: Path):
    summarize_segment(tmp_path, _report("models", "degraded", degraded=[
        {"step": "weather", "status": "failed", "message": "daily quota",
         "category": "upstream_quota", "self_heals": True, "segment": "models"}]))
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[
        {"step": "pbp", "status": "timeout", "message": "exceeded 1800s",
         "category": "timeout", "self_heals": False, "segment": "analytics"}]))
    summarize_segment(tmp_path, _report("stats", "failed", required=[
        {"step": "cfbd-current-player-stats", "status": "failed", "message": "0 rows",
         "category": "empty_result", "self_heals": False, "segment": "stats"}]))

    order = [i["step"] for i in attention_items(tmp_path)]
    assert order == ["cfbd-current-player-stats", "pbp", "weather"]


def test_overall_reads_self_healing_apart_from_a_real_degrade(tmp_path: Path):
    summarize_segment(tmp_path, _report("core", "success"))
    summarize_segment(tmp_path, _report("models", "degraded", degraded=[
        {"step": "weather", "status": "failed", "message": "daily quota",
         "category": "upstream_quota", "self_heals": True, "segment": "models"}]))

    verdict = overall(tmp_path)
    assert verdict["state"] == "self_healing"
    assert verdict["actionable_count"] == 0
    assert verdict["self_healing_count"] == 1


def test_not_configured_is_reported_with_its_remedy_but_not_as_a_failure(tmp_path: Path):
    summarize_segment(tmp_path, _report("content", "success", skipped=[
        {"step": "youtube", "message": "needs YOUTUBE_API_KEY",
         "category": "not_configured", "segment": "content"}]))

    assert overall(tmp_path)["state"] == "healthy"
    nc = not_configured_items(tmp_path)
    assert nc[0]["step"] == "youtube"
    assert nc[0]["rerun_segment"] == "content"


def test_a_skipped_report_changes_nothing(tmp_path: Path):
    summarize_segment(tmp_path, _report("core", "success"))
    summarize_segment(tmp_path, {"status": "skipped", "profile": "core",
                                 "reason": "refresh_already_running"})
    assert read_health(tmp_path)["core"]["last_status"] == "success"


def test_only_newly_opened_actionable_issues_notify(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(refresh_health, "_announce",
                        lambda segment, issues: calls.append((segment, [i["step"] for i in issues])))

    pbp = {"step": "pbp", "status": "timeout", "message": "exceeded 1800s",
           "category": "timeout", "self_heals": False, "segment": "analytics"}
    quota = {"step": "weather", "status": "failed", "message": "daily quota",
             "category": "upstream_quota", "self_heals": True, "segment": "analytics"}

    # First degrade: pbp is new and actionable, weather self-heals.
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[pbp, quota]))
    assert calls == [("analytics", ["pbp"])]

    # Same issue next run: no fresh notification.
    calls.clear()
    summarize_segment(tmp_path, _report("analytics", "degraded", degraded=[pbp, quota],
                                        finished="2026-09-08T12:00:00+00:00"))
    assert calls == []


def test_the_webhook_is_opt_in(tmp_path: Path, monkeypatch):
    posted = []
    monkeypatch.setattr(refresh_health, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted with no webhook")))
    monkeypatch.delenv("CFB_ALERT_WEBHOOK", raising=False)
    refresh_health._announce("analytics", [
        {"step": "pbp", "category": "timeout", "self_heals": False}])
    assert posted == []


def test_older_reports_without_categories_still_classify(tmp_path: Path):
    # A history row written before the rework: degraded_steps has no category.
    summarize_segment(tmp_path, _report("content", "degraded", degraded=[
        {"step": "bluesky", "status": "failed", "message": "stored 0 rows"}]))
    issue = read_health(tmp_path)["content"]["open_issues"][0]
    assert issue["category"] == "empty_result"
