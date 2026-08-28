"""Install lightweight rivalry annotation on detached repository game rows.

This stays outside repository.py so the canonical SQLite model remains about
CFBD data.  The wrappers only decorate dicts returned to the web layer; no
stored game row is rewritten.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.rivalries import annotate_game, annotate_games


def _wrap_many(name: str) -> None:
    original = getattr(CFBRepository, name, None)
    if original is None or getattr(original, "_rivalry_annotated", False):
        return

    @wraps(original)
    def wrapped(self: CFBRepository, *args: Any, **kwargs: Any):
        rows = original(self, *args, **kwargs)
        if not rows:
            return rows
        return annotate_games(rows)

    wrapped._rivalry_annotated = True  # type: ignore[attr-defined]
    setattr(CFBRepository, name, wrapped)


def _wrap_one(name: str) -> None:
    original = getattr(CFBRepository, name, None)
    if original is None or getattr(original, "_rivalry_annotated", False):
        return

    @wraps(original)
    def wrapped(self: CFBRepository, *args: Any, **kwargs: Any):
        row = original(self, *args, **kwargs)
        return annotate_game(row) if row else row

    wrapped._rivalry_annotated = True  # type: ignore[attr-defined]
    setattr(CFBRepository, name, wrapped)


def install_rivalry_annotations() -> None:
    """Decorate the game-returning methods used by current web pages."""
    for name in ("team_schedule", "conference_games", "upcoming_games"):
        _wrap_many(name)
    _wrap_one("get_game")
