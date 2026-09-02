from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from app import create_app


def _app(tmp_path: Path) -> Flask:
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
    })


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
