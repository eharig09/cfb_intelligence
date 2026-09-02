"""Install lightweight rivalry annotation on detached repository game rows.

This stays outside repository.py so the canonical SQLite model remains about
CFBD data. The wrappers only decorate dicts returned to the web layer; no
stored game row is rewritten.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

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


def _rivalry_label(rivalry: dict[str, Any] | None) -> str | None:
    if not rivalry:
        return None
    label = f"★ {rivalry.get('name') or 'Rivalry game'}"
    trophy = rivalry.get("trophy")
    if trophy and trophy.lower() not in label.lower():
        label += f" · {trophy}"
    return label


def _install_view_annotations() -> None:
    """Add rivalry/TBD display context to shared schedule tables."""
    from sports_aggregator.cfb import views

    original_schedule = views.schedule_table
    if not getattr(original_schedule, "_rivalry_annotated", False):
        @wraps(original_schedule)
        def schedule_table(schedule, *args: Any, **kwargs: Any):
            games = list(schedule)
            table = original_schedule(games, *args, **kwargs)
            # Matched on game id, not position. The table no longer emits one
            # row per game in the order it was given them -- bye weeks have a
            # row and no game -- and zipping put one team's rivalry badge on
            # another team's fixture.
            by_id = {game.get("game_id"): game for game in games}
            for row in table.rows:
                game = by_id.get(row.get("game_id"))
                if game is None:
                    continue
                # CFBD uses a midnight timestamp as a placeholder when kickoff
                # time has not been announced. Preserve real midnight/noon
                # timestamps, but do not present a TBD placeholder as 12:00 AM.
                if game.get("start_time_tbd"):
                    row["date_sub"] = "TBD"
                label = _rivalry_label(game.get("rivalry"))
                if label:
                    row["opponent_sub"] = label
                    row["opponent_class"] = "rivalry"
            return table

        schedule_table._rivalry_annotated = True  # type: ignore[attr-defined]
        views.schedule_table = schedule_table

    original_games = views.games_table
    if not getattr(original_games, "_rivalry_annotated", False):
        @wraps(original_games)
        def games_table(games, *args: Any, **kwargs: Any):
            items = list(games)
            table = original_games(items, *args, **kwargs)
            by_id = {game.get("game_id"): game for game in items}
            for row in table.rows:
                game = by_id.get(row.get("game_id"))
                if game is None:
                    continue
                if game.get("start_time_tbd"):
                    row["date_sub"] = "TBD"
                label = _rivalry_label(game.get("rivalry"))
                if label:
                    # One label is enough; putting it beneath both teams makes
                    # a two-team matchup look like two separate rivalry facts.
                    row["home_team_sub"] = label
                    row["home_team_class"] = "rivalry"
            return table

        games_table._rivalry_annotated = True  # type: ignore[attr-defined]
        views.games_table = games_table


def install_rivalry_annotations() -> None:
    """Decorate current game-returning methods and shared schedule renderers."""
    for name in ("team_schedule", "conference_games", "upcoming_games"):
        _wrap_many(name)
    _wrap_one("get_game")
    _install_view_annotations()
