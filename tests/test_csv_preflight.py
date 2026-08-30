from __future__ import annotations

from pathlib import Path

from sports_aggregator.cfb.cfbdepth_flexible import (
    canonicalize_cfbdepth_upload,
    preflight_cfbdepth_upload,
)
from sports_aggregator.cfb.pff_flexible import PRIMARY_SIGNATURES, preflight_pff_directory


def test_pff_preflight_uses_headers_not_download_filenames(tmp_path: Path):
    for index, (dataset, signature) in enumerate(PRIMARY_SIGNATURES.items(), start=1):
        path = tmp_path / f"random-download-{index}.csv"
        path.write_text(",".join(sorted(signature)) + "\n" + ",".join("1" for _ in signature) + "\n")

    report = preflight_pff_directory(tmp_path)

    assert report.ready is True
    assert set(report.primary) == set(PRIMARY_SIGNATURES)
    assert report.missing_primary == ()
    assert report.duplicates == {}


def test_pff_preflight_rejects_duplicate_dataset_matches(tmp_path: Path):
    signature = sorted(PRIMARY_SIGNATURES["passing"])
    content = ",".join(signature) + "\n" + ",".join("1" for _ in signature) + "\n"
    (tmp_path / "passing-one.csv").write_text(content)
    (tmp_path / "passing-two.csv").write_text(content)

    report = preflight_pff_directory(tmp_path)

    assert report.ready is False
    assert "primary:passing" in report.duplicates


def test_cfbdepth_upload_normalizes_header_punctuation():
    raw = (
        "school,conference,active_players,transfers,transfer %,home_grown,home grown %,"
        "5 star,4 star,3 star,2 star,0 star,blue_chip %,ol_avg_wt,dl avg wt,roster avg wt\n"
        "Example U,Test,85,12,14.1,73,85.9,1,10,30,20,24,12.9,315,292,228\n"
    )

    check, canonical = canonicalize_cfbdepth_upload(raw, expected_kind="roster")

    assert check.ready is True
    assert "Active Players" in canonical.splitlines()[0]
    assert "Example U" in canonical


def test_cfbdepth_upload_rejects_wrong_slot():
    updates = "Name,Team,Status,Last Update,Update\nPlayer One,Example U,Out,2026-08-30,injury\n"

    check = preflight_cfbdepth_upload(updates, expected_kind="roster")

    assert check.ready is False
    assert check.missing_required
