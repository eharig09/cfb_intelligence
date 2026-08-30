"""Flexible discovery/preflight for new PFF CSV export batches.

PFF download filenames are not stable: browser downloads commonly add numeric
suffixes even when the export schema is identical.  The core importer remains
the trusted database writer; this module identifies files by their headers and
stages them under the canonical names that importer already understands.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any

from sports_aggregator.cfb.pff import DATASETS, SUPPLEMENTAL_DATASETS, PFFImporter


# Dataset recognition intentionally uses distinctive columns rather than exact
# complete schemas so harmless PFF additions do not break imports.
PRIMARY_SIGNATURES: dict[str, frozenset[str]] = {
    "coverage": frozenset({"player_id", "player", "team_name", "position", "grades_coverage_defense", "snap_counts_coverage"}),
    "defense": frozenset({"player_id", "player", "team_name", "position", "grades_defense", "snap_counts_defense"}),
    "blocking": frozenset({"player_id", "player", "team_name", "position", "snap_counts_offense", "grades_pass_block", "grades_run_block"}),
    "passing": frozenset({"player_id", "player", "team_name", "position", "grades_pass", "dropbacks"}),
    "pass_rush": frozenset({"player_id", "player", "team_name", "position", "grades_pass_rush_defense", "snap_counts_pass_rush"}),
    "receiving": frozenset({"player_id", "player", "team_name", "position", "grades_pass_route", "routes"}),
    "rushing": frozenset({"player_id", "player", "team_name", "position", "grades_run", "attempts"}),
}

SUPPLEMENTAL_SIGNATURES: dict[str, frozenset[str]] = {
    "coverage_scheme": frozenset({"player_id", "player", "team_name", "man_grades_coverage_defense", "zone_grades_coverage_defense"}),
    "passing_depth": frozenset({"player_id", "player", "team_name", "short_grades_pass", "medium_grades_pass", "deep_grades_pass"}),
    "receiving_scheme": frozenset({"player_id", "player", "team_name", "man_grades_pass_route", "zone_grades_pass_route"}),
    "returns": frozenset({"player_id", "player", "team_name", "grades_return", "total_attempts"}),
    "run_defense_detail": frozenset({"player_id", "player", "team_name", "grades_run_defense", "snap_counts_run"}),
}

CANONICAL_PRIMARY = {dataset: filename for filename, (dataset, _grade, _usage) in DATASETS.items()}
CANONICAL_SUPPLEMENTAL = {dataset: filename for filename, (dataset, _fields) in SUPPLEMENTAL_DATASETS.items()}


@dataclass(frozen=True, slots=True)
class PFFBatchPreflight:
    directory: str
    primary: dict[str, str]
    supplemental: dict[str, str]
    missing_primary: tuple[str, ...]
    duplicates: dict[str, tuple[str, ...]]
    unclassified: tuple[str, ...]
    csv_files: int

    @property
    def ready(self) -> bool:
        return not self.missing_primary and not self.duplicates

    def as_dict(self) -> dict[str, Any]:
        packet = asdict(self)
        packet["ready"] = self.ready
        return packet


def _headers(path: Path) -> set[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            return {str(value).strip() for value in next(reader, []) if str(value).strip()}
    except (OSError, UnicodeError, csv.Error):
        return set()


def _classify(headers: set[str]) -> tuple[str, str] | None:
    # Supplemental schemas can contain generic summary columns too, so test the
    # more specific shapes first.
    for dataset, signature in SUPPLEMENTAL_SIGNATURES.items():
        if signature.issubset(headers):
            return "supplemental", dataset
    for dataset, signature in PRIMARY_SIGNATURES.items():
        if signature.issubset(headers):
            return "primary", dataset
    return None


def preflight_pff_directory(directory: str | Path) -> PFFBatchPreflight:
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"PFF export directory does not exist: {root}")

    found: dict[tuple[str, str], list[Path]] = {}
    unclassified: list[str] = []
    csv_paths = sorted(path for path in root.glob("*.csv") if path.is_file())
    for path in csv_paths:
        classification = _classify(_headers(path))
        if classification is None:
            unclassified.append(path.name)
            continue
        found.setdefault(classification, []).append(path)

    duplicates: dict[str, tuple[str, ...]] = {}
    primary: dict[str, str] = {}
    supplemental: dict[str, str] = {}
    for (kind, dataset), paths in found.items():
        names = tuple(path.name for path in paths)
        if len(paths) > 1:
            duplicates[f"{kind}:{dataset}"] = names
            continue
        target = primary if kind == "primary" else supplemental
        target[dataset] = str(paths[0])

    missing = tuple(sorted(set(PRIMARY_SIGNATURES) - set(primary)))
    return PFFBatchPreflight(
        directory=str(root), primary=primary, supplemental=supplemental,
        missing_primary=missing, duplicates=duplicates,
        unclassified=tuple(unclassified), csv_files=len(csv_paths),
    )


def import_pff_directory_flexible(repository, directory: str | Path, *, season: int,
                                  roster_season: int):
    """Validate a fresh PFF export directory, then run the established importer."""
    root = Path(directory)
    check = preflight_pff_directory(root)
    if not check.ready:
        problems = []
        if check.missing_primary:
            problems.append("missing primary datasets: " + ", ".join(check.missing_primary))
        if check.duplicates:
            duplicate_text = "; ".join(
                f"{dataset}=[{', '.join(files)}]" for dataset, files in sorted(check.duplicates.items())
            )
            problems.append("duplicate dataset matches: " + duplicate_text)
        raise ValueError("PFF preflight failed: " + "; ".join(problems))

    with TemporaryDirectory(prefix="cfb-pff-") as temp:
        staging = Path(temp)
        for dataset, source in check.primary.items():
            shutil.copy2(source, staging / CANONICAL_PRIMARY[dataset])
        for dataset, source in check.supplemental.items():
            shutil.copy2(source, staging / CANONICAL_SUPPLEMENTAL[dataset])

        # Preserve optional historical OL context when the batch carries it.
        history = root / "oline_data"
        if history.is_dir():
            shutil.copytree(history, staging / "oline_data")

        return PFFImporter(repository).import_directory(
            staging, season=int(season), roster_season=int(roster_season)
        )
