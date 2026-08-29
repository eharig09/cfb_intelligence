"""Inject sourced injury annotations into the player Career Path timeline."""

from __future__ import annotations

from html import escape

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.player_injuries import events_for_player


PLAYER_STINT_ANCHOR = (
    "                    <div class=\"meta\">{{ stint.position or '—' }} &middot; "
    "class {{ stint.class_year or '—' }}</div>\n"
)
PLAYER_STINT_INSERT = (
    PLAYER_STINT_ANCHOR
    + "                    {{ player_injury_notes(player.player_id, stint.season, player.stints | length) }}\n"
)


class _PlayerInjuryTemplateLoader(BaseLoader):
    def __init__(self, wrapped: BaseLoader):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_player.html" and "player_injury_notes(" not in source:
            source = source.replace(PLAYER_STINT_ANCHOR, PLAYER_STINT_INSERT, 1)
        return source, filename, uptodate

    def list_templates(self):
        if hasattr(self.wrapped, "list_templates"):
            return self.wrapped.list_templates()
        return []


def _notes(repository, player_id: str, stint_season: int, stint_count: int) -> Markup:
    # Incoming/new recruits have no college injury history to annotate.
    if int(stint_count or 0) < 2:
        return Markup("")
    rows = [
        row for row in events_for_player(repository, str(player_id), through_season=int(stint_season))
        if int(row["season"]) == int(stint_season)
    ]
    if not rows:
        return Markup("")

    rendered = []
    seen = set()
    for row in rows:
        key = (row.get("injury_label"), row.get("body_part"), row.get("source_url"))
        if key in seen:
            continue
        seen.add(key)
        label = escape(str(row.get("injury_label") or "Injury"))
        qualifier = "season-ending" if row.get("season_ending") else str(row.get("confidence") or "reported")
        source = escape(str(row.get("source_name") or "Source"))
        url = escape(str(row.get("source_url") or ""), quote=True)
        rendered.append(
            "<div class='meta injury-career-note' style='margin-top:.35rem'>"
            "<span aria-hidden='true'>⚕</span> "
            f"<strong>{label}</strong> · {escape(qualifier)} · "
            f"<a href='{url}' rel='noopener noreferrer'>{source}</a>"
            "</div>"
        )
    return Markup("".join(rendered))


def install_player_injury_display(app) -> None:
    if app.extensions.get("player_injury_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["player_injury_notes"] = (
        lambda player_id, stint_season, stint_count: _notes(
            repository, str(player_id), int(stint_season), int(stint_count)
        )
    )
    app.jinja_loader = _PlayerInjuryTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["player_injury_display_installed"] = True
