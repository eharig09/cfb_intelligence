"""Presentation-neutral tabular contract shared by HTML views and JSON APIs.

Templates previously formatted every statistic inline with ad-hoc `%g`, `%.1f`,
and `value * 100 if value <= 1` expressions, so the same number rendered
differently on different pages and alignment was decided per template. A view
now describes *what* a column is once, and one macro decides how it looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


#: Column formats. "pct" expects 0-100; "rate" expects a 0-1 fraction.
NUMERIC_FORMATS = frozenset(
    {"int", "big", "num", "f1", "f2", "f3", "pct", "rate", "rank", "signed"}
)


@dataclass(frozen=True)
class Column:
    """One column definition: identity, header label, and display intent."""

    key: str
    label: str
    format: str = "text"
    align: str | None = None
    title: str | None = None
    emphasis: bool = False

    def __post_init__(self) -> None:
        if self.align is None:
            object.__setattr__(self, "align", "right" if self.numeric else "left")

    @property
    def numeric(self) -> bool:
        return self.format in NUMERIC_FORMATS


@dataclass
class Table:
    """A rendered table: columns, dict rows, and the copy shown when empty."""

    columns: Sequence[Column]
    rows: list[dict[str, Any]] = field(default_factory=list)
    caption: str | None = None
    note: str | None = None
    empty: str = "No data is stored for this view yet."
    total_row: dict[str, Any] | None = None
    dense: bool = False

    def __bool__(self) -> bool:
        return bool(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the JSON API so clients receive the same column order."""
        return {
            "caption": self.caption,
            "note": self.note,
            "columns": [
                {"key": column.key, "label": column.label, "format": column.format,
                 "align": column.align, "title": column.title}
                for column in self.columns
            ],
            "rows": self.rows,
            "total_row": self.total_row,
        }


def format_value(value: Any, fmt: str = "text") -> str:
    """Render one cell. Missing data is an em dash, never a blank or a zero."""
    if value is None or value == "":
        return "—"
    if fmt in {"text", "rank"}:
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt == "int":
        # Sports tables read better without separators; counts use "big" instead.
        return str(round(number))
    if fmt == "big":
        return f"{round(number):,}"
    if fmt == "num":
        return f"{number:g}"
    if fmt == "f1":
        return f"{number:.1f}"
    if fmt == "f2":
        return f"{number:.2f}"
    if fmt == "f3":
        return f"{number:.3f}"
    if fmt == "pct":
        return f"{number:.1f}%"
    if fmt == "rate":
        # Source publishes a 0-1 fraction; "pct" is for values already 0-100.
        return f"{number * 100:.1f}%"
    if fmt == "signed":
        return f"{number:+g}"
    return str(value)


def build_rows(records: Iterable[Any], mapping: dict[str, str]) -> list[dict[str, Any]]:
    """Project mapping-like records onto column keys without touching templates."""
    rows: list[dict[str, Any]] = []
    for record in records:
        source = record if isinstance(record, dict) else dict(record)
        rows.append({key: source.get(field_name) for key, field_name in mapping.items()})
    return rows
