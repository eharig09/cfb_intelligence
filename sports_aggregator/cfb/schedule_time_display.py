"""Narrow display correction for schedule-table noon kickoffs.

The canonical game timestamp remains untouched. This only corrects the rendered
schedule-table label when a non-TBD row has arrived as the known 12:00 AM display
artifact for a 1200 kickoff.
"""

from __future__ import annotations

from typing import Any, Iterable

from sports_aggregator.cfb import views


def _correct_noon_label(game: dict[str, Any]) -> dict[str, Any]:
    item = dict(game)
    label = str(item.get("time_label") or "")
    if not item.get("start_time_tbd") and label.startswith("12:00 AM"):
        item["time_label"] = label.replace("12:00 AM", "12:00 PM", 1)
    return item


def install_schedule_noon_display_fix() -> None:
    original = views.schedule_table
    if getattr(original, "_noon_display_fixed", False):
        return

    def wrapped(schedule: Iterable[dict[str, Any]], *args: Any, **kwargs: Any):
        corrected = [_correct_noon_label(game) for game in schedule]
        return original(corrected, *args, **kwargs)

    wrapped._noon_display_fixed = True  # type: ignore[attr-defined]
    views.schedule_table = wrapped
