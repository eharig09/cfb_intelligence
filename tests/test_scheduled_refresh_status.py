from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sports_aggregator.scheduled_refresh import run_scheduled_refresh


def _runner_with_log(lines: list[str], returncode: int = 0):
    def runner(command, *, cwd, stdout, stderr, text):
        for line in lines:
            stdout.write(line + "\n")
        stdout.flush()
        return SimpleNamespace(returncode=returncode)
    return runner


def test_optional_step_failure_marks_refresh_degraded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(tmp_path / "instance"))
    runner = _runner_with_log([
        "[ ] cfbd-lines: Fast market-only betting-line refresh",
        "[ok] success (0.5s) lines=42",
        "[ ] bluesky: Curated Bluesky author feeds",
        "[!!] failed (1.2s) endpoints=18 succeeded=17 seen=250 stored=200 errors=1",
        "refresh: 22 steps, 0 failed",
    ])

    report = run_scheduled_refresh(2026, repo_root=tmp_path, runner=runner)

    assert report["status"] == "degraded"
    assert report["exit_code"] == 0
    assert report["degraded_count"] == 1
    assert report["degraded_steps"][0]["step"] == "bluesky"
    assert report["degraded_steps"][0]["status"] == "failed"


def test_clean_optional_steps_keep_refresh_successful(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(tmp_path / "instance"))
    runner = _runner_with_log([
        "[ ] bluesky: Curated Bluesky author feeds",
        "[ok] success (1.0s) endpoints=18 succeeded=18 seen=250 stored=200 errors=0",
        "refresh: 22 steps, 0 failed",
    ])

    report = run_scheduled_refresh(2026, repo_root=tmp_path, runner=runner)

    assert report["status"] == "success"
    assert report["degraded_count"] == 0
    assert report["degraded_steps"] == []
