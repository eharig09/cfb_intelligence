from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from app import create_app


def _app(tmp_path: Path, **overrides) -> Flask:
    """The real application, not a Flask instance with one blueprint on it.

    The page extends the shared layout, and the layout links the national RSS
    feed, the index and the static bundle. A minimal app can build none of
    those, so both tests here died in `url_for` before reaching anything they
    were written to check.
    """
    return create_app({
        "TESTING": True,
        "REGISTER_LEGACY_DASHBOARDS": False,
        "CFB_DATABASE_PATH": str(tmp_path / "cfb.sqlite3"),
        "CFB_DISPLAY_TIMEZONE": "America/New_York",
        **overrides,
    })


def _now_iso(**delta) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _write_health(tmp_path: Path, health: dict) -> None:
    (tmp_path / "segment_health.json").write_text(json.dumps(health), encoding="utf-8")


def test_data_status_renders_sanitized_refresh_metadata(tmp_path: Path):
    progress = {
        "season": 2026,
        "profile": "light",
        "started_at": "2026-08-27T14:00:00+00:00",
        "finished_at": "2026-08-27T14:05:00+00:00",
        "completed": True,
        "steps": {
            "weather": {
                "status": "success",
                "at": "2026-08-27T14:03:00+00:00",
                "message": "9 forecasts updated",
                "updated": 9,
            },
            "reddit": {
                "status": "success",
                "at": "2026-08-27T14:04:00+00:00",
                "added": 18,
            },
        },
    }
    (tmp_path / "refresh_progress.json").write_text(json.dumps(progress), encoding="utf-8")
    (tmp_path / "scheduled_refresh_history.jsonl").write_text(
        json.dumps({
            "status": "success",
            "profile": "light",
            "season": 2026,
            "started_at": "2026-08-27T14:00:00+00:00",
            "finished_at": "2026-08-27T14:05:00+00:00",
            "seconds": 300.0,
            "step_count": 2,
            "log": "/var/data/refresh_logs/private.log",
            "parent_peak_rss_mb": 33.4,
        }) + "\n",
        encoding="utf-8",
    )

    response = _app(tmp_path).test_client().get("/college-football/data-status/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Data Status" in body
    assert "Weather" in body
    assert "Reddit" in body
    assert "18 added" in body
    assert "9 updated" in body
    assert "/var/data/refresh_logs/private.log" not in body
    assert "parent_peak_rss_mb" not in body


def test_data_status_handles_empty_history(tmp_path: Path):
    response = _app(tmp_path).test_client().get("/college-football/data-status/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No per-section refresh history has been recorded yet" in body
    assert "No completed refresh history is available yet" in body


def test_a_degraded_step_reaches_the_page_with_a_next_step(tmp_path: Path):
    _write_health(tmp_path, {
        "analytics": {
            "segment": "analytics", "last_run_at": _now_iso(hours=4),
            "last_status": "degraded", "last_success_at": _now_iso(days=1),
            "consecutive_degraded": 2, "not_configured": [],
            "open_issues": [{
                "step": "pbp", "status": "timeout", "message": "exceeded 1800s",
                "severity": "degraded", "category": "timeout", "self_heals": False,
                "since": _now_iso(days=1), "segment": "analytics",
            }],
        },
    })

    body = _app(tmp_path).test_client().get(
        "/college-football/data-status/").get_data(as_text=True)

    assert "Needs attention" in body
    assert "pbp" in body and "exceeded 1800s" in body
    assert "Next step" in body
    # the concrete remediation, not just the word "degraded"
    assert "segment=analytics" in body
    assert 'data-rerun="analytics"' in body
    assert 'data-logtail="analytics"' in body


def test_a_self_healing_quota_does_not_read_as_needing_action(tmp_path: Path):
    _write_health(tmp_path, {
        "core": {"segment": "core", "last_run_at": _now_iso(hours=1),
                 "last_status": "success", "last_success_at": _now_iso(hours=1),
                 "consecutive_degraded": 0, "open_issues": [], "not_configured": []},
        "models": {
            "segment": "models", "last_run_at": _now_iso(hours=3),
            "last_status": "degraded", "last_success_at": _now_iso(hours=3),
            "consecutive_degraded": 1, "not_configured": [],
            "open_issues": [{
                "step": "weather", "status": "failed",
                "message": "Daily API request limit exceeded.",
                "severity": "degraded", "category": "upstream_quota",
                "self_heals": True, "since": _now_iso(hours=3), "segment": "models",
            }],
        },
    })

    with _app(tmp_path).test_request_context("/anything"):
        from sports_aggregator.cfb.data_status import inject_data_freshness
        freshness = inject_data_freshness()["data_freshness"]

    assert freshness["status"] == "success"
    assert freshness["attention_count"] == 0


def test_the_segment_grid_lists_every_segment_even_the_unrun_ones(tmp_path: Path):
    _write_health(tmp_path, {
        "core": {"segment": "core", "last_run_at": _now_iso(hours=2),
                 "last_status": "success", "last_success_at": _now_iso(hours=2),
                 "consecutive_degraded": 0, "open_issues": [], "not_configured": []},
    })
    body = _app(tmp_path).test_client().get(
        "/college-football/data-status/").get_data(as_text=True)

    assert "Segment health" in body
    for segment in ("core", "rosters", "stats", "models", "content", "analytics", "news"):
        assert segment in body
    assert "No run recorded" in body  # the six that have not run


def test_the_stale_change_ledger_is_not_shown(tmp_path: Path):
    (tmp_path / "refresh_change_history.jsonl").write_text(json.dumps({
        "finished_at": "2026-08-01T10:00:00+00:00", "profile": "news",
        "totals": {"added": 53, "changed": 1952, "removed": 0}, "changes": [],
    }) + "\n", encoding="utf-8")

    body = _app(tmp_path).test_client().get(
        "/college-football/data-status/").get_data(as_text=True)

    assert "What changed in the latest tracked run" not in body
    assert "1952" not in body


def test_a_fresh_change_ledger_still_renders(tmp_path: Path):
    (tmp_path / "refresh_change_history.jsonl").write_text(json.dumps({
        "finished_at": _now_iso(hours=2), "profile": "news",
        "totals": {"added": 4, "changed": 9, "removed": 0}, "changes": [],
    }) + "\n", encoding="utf-8")

    body = _app(tmp_path).test_client().get(
        "/college-football/data-status/").get_data(as_text=True)

    assert "What changed in the latest tracked run" in body


def test_the_log_tail_route_is_behind_the_token(tmp_path: Path):
    app = _app(tmp_path, CFB_REFRESH_TOKEN="s3cr3t")
    (tmp_path / "scheduled_refresh_history.jsonl").write_text(json.dumps({
        "status": "degraded", "profile": "analytics",
        "finished_at": _now_iso(hours=1),
        "log": str(tmp_path / "refresh_logs" / "refresh-x.log"),
    }) + "\n", encoding="utf-8")
    (tmp_path / "refresh_logs").mkdir()
    (tmp_path / "refresh_logs" / "refresh-x.log").write_text(
        "line one\n[!!] pbp: exceeded 1800s\n", encoding="utf-8")

    client = app.test_client()
    assert client.get("/college-football/data-status/log-tail?segment=analytics").status_code == 401

    ok = client.get("/college-football/data-status/log-tail?segment=analytics",
                    headers={"Authorization": "Bearer s3cr3t"})
    assert ok.status_code == 200
    payload = ok.get_json()
    assert payload["log"] == "refresh-x.log"
    assert any("exceeded 1800s" in line for line in payload["lines"])


def test_the_admin_pin_works_on_the_status_page_like_it_does_on_the_refresh(tmp_path: Path):
    # The re-run button authenticates through require_refresh_auth, which takes
    # the PIN; the log tail rejected it, on the same page.
    app = _app(tmp_path, CFB_REFRESH_TOKEN="s3cr3t", CFB_ADMIN_PIN="1234")
    (tmp_path / "scheduled_refresh_history.jsonl").write_text(json.dumps({
        "status": "degraded", "profile": "content", "finished_at": _now_iso(hours=1),
        "log": str(tmp_path / "refresh_logs" / "r.log"),
    }) + "\n", encoding="utf-8")
    (tmp_path / "refresh_logs").mkdir(); (tmp_path / "refresh_logs" / "r.log").write_text("x\n", encoding="utf-8")
    assert app.test_client().get("/college-football/data-status/log-tail?segment=content",
                                 headers={"Authorization": "Bearer nope"}).status_code == 401
    assert app.test_client().get("/college-football/data-status/log-tail?segment=content",
                                 headers={"Authorization": "Bearer 1234"}).status_code == 200
