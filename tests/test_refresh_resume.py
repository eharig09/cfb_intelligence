"""Progress that survives the run being killed.

Every step is already its own process and commits before the next one starts,
so the work a killed run did is on disk. What was missing was any memory of
it: the next run began at step one and spent its budget redoing what was
already done, which on a 512 MB instance is how a refresh fails repeatedly
without ever reaching the end.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sports_aggregator.scheduled_refresh import (
    RESUME_WINDOW_HOURS, _read_progress, _resumable_steps, run_scheduled_refresh,
)


def _runner(results, captured):
    def run(phase, season, *, only=None, datasets=None):
        captured.append({"only": only, "datasets": datasets})
        return results
    return run


def _steps(*names, status="success"):
    return [{"step": name, "status": status, "message": "", "seconds": 0.1,
             "optional": False} for name in names]


def _write(instance: Path, record: dict) -> None:
    instance.mkdir(parents=True, exist_ok=True)
    (instance / "refresh_progress.json").write_text(
        json.dumps(record), encoding="utf-8")


def _record(**overrides):
    record = {
        "season": 2026, "profile": "heavy", "completed": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": {"cfbd-sync": {"status": "success"},
                  "articles": {"status": "failed"}},
    }
    record.update(overrides)
    return record


# -- what counts as resumable ---------------------------------------------

def test_a_killed_run_leaves_its_successes_behind(tmp_path: Path):
    _write(tmp_path, _record())
    resumable = _resumable_steps(tmp_path, 2026, "heavy",
                                 window_hours=RESUME_WINDOW_HOURS,
                                 now=datetime.now(timezone.utc))
    assert resumable == {"cfbd-sync"}, "a failed step is not skipped"


def test_a_run_that_finished_is_not_resumed_from(tmp_path: Path):
    """Otherwise the next refresh would skip the entire plan."""
    _write(tmp_path, _record(completed=True))
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12,
                            now=datetime.now(timezone.utc)) == set()


def test_stale_progress_is_ignored(tmp_path: Path):
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    _write(tmp_path, _record(started_at=old.isoformat()))
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12,
                            now=datetime.now(timezone.utc)) == set()


def test_another_season_or_profile_is_ignored(tmp_path: Path):
    now = datetime.now(timezone.utc)
    _write(tmp_path, _record(season=2025))
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12, now=now) == set()
    _write(tmp_path, _record(profile="light"))
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12, now=now) == set()


def test_resume_can_be_turned_off(tmp_path: Path):
    _write(tmp_path, _record())
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=0,
                            now=datetime.now(timezone.utc)) == set()


def test_a_missing_or_corrupt_file_is_not_an_error(tmp_path: Path):
    now = datetime.now(timezone.utc)
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12, now=now) == set()
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "refresh_progress.json").write_text("{not json", encoding="utf-8")
    assert _resumable_steps(tmp_path, 2026, "heavy", window_hours=12, now=now) == set()


# -- what the driver writes ------------------------------------------------

def test_progress_is_written_as_the_run_goes(tmp_path: Path, monkeypatch):
    """Written per step, because the reader is the next run after a kill."""
    instance = tmp_path / "instance"
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(instance))
    captured: list[dict] = []
    run_scheduled_refresh(2026, repo_root=tmp_path,
                          phase_runner=_runner(_steps("cfbd-sync"), captured))
    record = _read_progress(instance)
    assert record["season"] == 2026
    assert record["completed"] is True, "the plan was walked end to end"


def test_a_finished_run_clears_the_way_for_the_next_one(tmp_path: Path, monkeypatch):
    instance = tmp_path / "instance"
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(instance))
    captured: list[dict] = []
    run_scheduled_refresh(2026, repo_root=tmp_path,
                          phase_runner=_runner(_steps("cfbd-sync"), captured))
    assert _resumable_steps(instance, 2026, "heavy", window_hours=12,
                            now=datetime.now(timezone.utc)) == set()


def test_the_report_says_what_it_skipped(tmp_path: Path, monkeypatch):
    instance = tmp_path / "instance"
    _write(instance, _record())
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(instance))
    captured: list[dict] = []
    report = run_scheduled_refresh(2026, repo_root=tmp_path,
                                   phase_runner=_runner([], captured))
    assert report["resumed_steps"] == ["cfbd-sync"]


# -- running named steps ---------------------------------------------------

def test_only_overrides_the_profile_plan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CFB_REFRESH_STATE_PATH", str(tmp_path / "instance"))
    captured: list[dict] = []
    run_scheduled_refresh(2026, profile="light", repo_root=tmp_path,
                          only=["weather"],
                          phase_runner=_runner(_steps("weather"), captured))
    assert captured[0]["only"] == ["weather"]


def test_the_cli_offers_the_step_names(capsys):
    from sports_aggregator.scheduled_refresh import main

    assert main(["--list-steps", "--season", "2026"]) == 0
    printed = capsys.readouterr().out
    assert "cfbd-sync" in printed
    assert "weather" in printed
    assert "optional" in printed
