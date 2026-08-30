"""Safe discovery and preflight for fresh CFBDepth CSV exports.

The underlying import functions replace snapshot tables, so classification and
required-column validation happen before any database write.  Header matching
is case/punctuation insensitive, allowing harmless export changes such as
`Active_Players` vs `Active Players` without teaching the importer a new schema.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
import re
from typing import Any

from sports_aggregator.cfb.cfbdepth_data import (
    import_player_updates,
    import_roster_breakdown,
    import_team_impact,
)


EXPORT_FIELDS: dict[str, tuple[str, ...]] = {
    "roster": (
        "School", "Conference", "Active Players", "Transfers", "Transfer%",
        "Home Grown", "Home Grown%", "5-Star", "4-Star", "3-Star", "2-Star",
        "0-Star", "Blue Chip%", "OL Avg Wt", "DL Avg Wt", "Roster Avg Wt",
    ),
    "impact": (
        "School", "Conference", "Injury Number", "Injury New", "OFS", "O", "D",
        "Q", "P", "S", "GTD", "OPT", "RET", "Injury Impact", "Impact PP",
    ),
    "updates": (
        "Abb", "Name", "Team", "Pos", "Status", "Rating", "Impact", "New",
        "Last Update", "Update",
    ),
}

# A small identifying subset is enough to classify the export; the complete
# expected fields are then reported so new provider changes are visible.
SIGNATURES: dict[str, tuple[str, ...]] = {
    "roster": ("School", "Active Players", "Transfers", "Blue Chip%"),
    "impact": ("School", "Injury Number", "Injury Impact", "Impact PP"),
    "updates": ("Name", "Team", "Status", "Last Update", "Update"),
}

# Fields required for a useful/safe import. Other known fields may be absent and
# will simply remain NULL in the database.
REQUIRED: dict[str, tuple[str, ...]] = {
    "roster": ("School",),
    "impact": ("School",),
    "updates": ("Name", "Team"),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _headers(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            return [str(value).strip() for value in next(csv.reader(source), [])]
    except (OSError, UnicodeError, csv.Error):
        return []


def _mapped_headers(headers: list[str], kind: str) -> dict[str, str]:
    source_by_key = {_key(header): header for header in headers if header}
    return {
        canonical: source_by_key[_key(canonical)]
        for canonical in EXPORT_FIELDS[kind]
        if _key(canonical) in source_by_key
    }


def classify_cfbdepth_csv(path: str | Path) -> str | None:
    csv_path = Path(path)
    headers = _headers(csv_path)
    for kind, signature in SIGNATURES.items():
        mapped = _mapped_headers(headers, kind)
        if all(field in mapped for field in signature):
            return kind
    # Provider exports occasionally omit optional signature columns. Fall back
    # to the minimal safe identity columns only when that uniquely identifies a
    # type.
    candidates = []
    for kind, required in REQUIRED.items():
        mapped = _mapped_headers(headers, kind)
        if all(field in mapped for field in required):
            candidates.append(kind)
    return candidates[0] if len(candidates) == 1 else None


@dataclass(frozen=True, slots=True)
class CFBDepthFileCheck:
    path: str
    kind: str | None
    rows: int
    mapped_fields: tuple[str, ...]
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.kind) and not self.missing_required and self.rows > 0

    def as_dict(self) -> dict[str, Any]:
        packet = asdict(self)
        packet["ready"] = self.ready
        return packet


def preflight_cfbdepth_file(path: str | Path) -> CFBDepthFileCheck:
    csv_path = Path(path)
    kind = classify_cfbdepth_csv(csv_path)
    headers = _headers(csv_path)
    mapped = _mapped_headers(headers, kind) if kind else {}
    rows = 0
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            rows = sum(1 for _ in csv.DictReader(source))
    except (OSError, UnicodeError, csv.Error):
        rows = 0
    required = REQUIRED.get(kind or "", ())
    expected = EXPORT_FIELDS.get(kind or "", ())
    missing_required = tuple(field for field in required if field not in mapped)
    missing_optional = tuple(field for field in expected if field not in mapped and field not in required)
    return CFBDepthFileCheck(
        path=str(csv_path), kind=kind, rows=rows,
        mapped_fields=tuple(mapped), missing_required=missing_required,
        missing_optional=missing_optional,
    )


def _canonical_text(path: Path, kind: str) -> str:
    headers = _headers(path)
    mapping = _mapped_headers(headers, kind)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(EXPORT_FIELDS[kind]), extrasaction="ignore")
    writer.writeheader()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for source_row in csv.DictReader(source):
            writer.writerow({canonical: source_row.get(original) for canonical, original in mapping.items()})
    return output.getvalue()


def import_cfbdepth_file(repository, path: str | Path) -> dict[str, Any]:
    """Preflight and import one CFBDepth export without guessing its type."""
    csv_path = Path(path)
    check = preflight_cfbdepth_file(csv_path)
    if not check.ready or not check.kind:
        raise ValueError(
            f"CFBDepth preflight failed for {csv_path.name}: kind={check.kind!r}, "
            f"rows={check.rows}, missing_required={list(check.missing_required)}"
        )
    canonical = _canonical_text(csv_path, check.kind)
    if check.kind == "roster":
        imported = import_roster_breakdown(repository, canonical)
    elif check.kind == "impact":
        imported = import_team_impact(repository, canonical)
    else:
        imported = import_player_updates(repository, canonical)
    return {**check.as_dict(), "imported": imported}


def preflight_cfbdepth_directory(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"CFBDepth export directory does not exist: {root}")
    checks = [preflight_cfbdepth_file(path) for path in sorted(root.glob("*.csv")) if path.is_file()]
    by_kind: dict[str, list[str]] = {}
    for check in checks:
        if check.kind:
            by_kind.setdefault(check.kind, []).append(check.path)
    duplicates = {kind: tuple(paths) for kind, paths in by_kind.items() if len(paths) > 1}
    return {
        "directory": str(root),
        "ready": bool(checks) and all(check.ready for check in checks) and not duplicates,
        "duplicates": duplicates,
        "files": [check.as_dict() for check in checks],
    }
