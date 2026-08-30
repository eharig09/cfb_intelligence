"""Safe discovery and preflight for fresh CFBDepth CSV exports.

The underlying import functions replace snapshot tables, so classification and
required-column validation happen before any database write. Header matching is
case/punctuation insensitive, allowing harmless export changes such as
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

SIGNATURES: dict[str, tuple[str, ...]] = {
    "roster": ("School", "Active Players", "Transfers", "Blue Chip%"),
    "impact": ("School", "Injury Number", "Injury Impact", "Impact PP"),
    "updates": ("Name", "Team", "Status", "Last Update", "Update"),
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "roster": ("School",),
    "impact": ("School",),
    "updates": ("Name", "Team"),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _decode(source: str | bytes) -> str:
    if isinstance(source, bytes):
        return source.decode("utf-8-sig", errors="replace")
    return str(source)


def _headers_from_text(text: str) -> list[str]:
    try:
        return [str(value).strip() for value in next(csv.reader(StringIO(text)), [])]
    except csv.Error:
        return []


def _headers(path: Path) -> list[str]:
    try:
        return _headers_from_text(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return []


def _mapped_headers(headers: list[str], kind: str) -> dict[str, str]:
    source_by_key = {_key(header): header for header in headers if header}
    return {
        canonical: source_by_key[_key(canonical)]
        for canonical in EXPORT_FIELDS[kind]
        if _key(canonical) in source_by_key
    }


def _classify_headers(headers: list[str]) -> str | None:
    for kind, signature in SIGNATURES.items():
        mapped = _mapped_headers(headers, kind)
        if all(field in mapped for field in signature):
            return kind
    candidates = []
    for kind, required in REQUIRED.items():
        mapped = _mapped_headers(headers, kind)
        if all(field in mapped for field in required):
            candidates.append(kind)
    return candidates[0] if len(candidates) == 1 else None


def classify_cfbdepth_csv(path: str | Path) -> str | None:
    return _classify_headers(_headers(Path(path)))


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


def _preflight_text(text: str, *, label: str = "upload.csv",
                    expected_kind: str | None = None) -> CFBDepthFileCheck:
    headers = _headers_from_text(text)
    kind = _classify_headers(headers)
    if expected_kind and kind and kind != expected_kind:
        # A file in the wrong browser slot is rejected instead of being imported
        # into an unrelated snapshot table.
        return CFBDepthFileCheck(
            path=label, kind=kind, rows=0, mapped_fields=tuple(),
            missing_required=(f"expected {expected_kind} export",), missing_optional=tuple(),
        )
    active_kind = expected_kind or kind
    mapped = _mapped_headers(headers, active_kind) if active_kind else {}
    try:
        rows = sum(1 for _ in csv.DictReader(StringIO(text)))
    except csv.Error:
        rows = 0
    required = REQUIRED.get(active_kind or "", ())
    expected = EXPORT_FIELDS.get(active_kind or "", ())
    missing_required = tuple(field for field in required if field not in mapped)
    missing_optional = tuple(field for field in expected if field not in mapped and field not in required)
    return CFBDepthFileCheck(
        path=label, kind=active_kind, rows=rows,
        mapped_fields=tuple(mapped), missing_required=missing_required,
        missing_optional=missing_optional,
    )


def preflight_cfbdepth_file(path: str | Path) -> CFBDepthFileCheck:
    csv_path = Path(path)
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        text = ""
    return _preflight_text(text, label=str(csv_path))


def preflight_cfbdepth_upload(source: str | bytes, *, expected_kind: str,
                              label: str = "upload.csv") -> CFBDepthFileCheck:
    return _preflight_text(_decode(source), label=label, expected_kind=expected_kind)


def canonicalize_cfbdepth_upload(source: str | bytes, *, expected_kind: str,
                                 label: str = "upload.csv") -> tuple[CFBDepthFileCheck, str]:
    """Validate and normalize one uploaded export before snapshot replacement."""
    text = _decode(source)
    check = _preflight_text(text, label=label, expected_kind=expected_kind)
    if not check.ready:
        raise ValueError(
            f"CFBDepth preflight failed for {label}: rows={check.rows}, "
            f"missing_required={list(check.missing_required)}"
        )
    headers = _headers_from_text(text)
    mapping = _mapped_headers(headers, expected_kind)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(EXPORT_FIELDS[expected_kind]), extrasaction="ignore")
    writer.writeheader()
    for source_row in csv.DictReader(StringIO(text)):
        writer.writerow({canonical: source_row.get(original) for canonical, original in mapping.items()})
    return check, output.getvalue()


def _canonical_text(path: Path, kind: str) -> str:
    _check, canonical = canonicalize_cfbdepth_upload(
        path.read_bytes(), expected_kind=kind, label=str(path)
    )
    return canonical


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
