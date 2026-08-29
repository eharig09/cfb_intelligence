"""Compact coordinator context for the team page.

The team hero shows OC/DC performance beneath the head coach and adds a single
staff-continuity fact card. All values come from stored coordinator/game data;
page rendering does not make network requests.
"""

from __future__ import annotations

from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.coordinator_context import coordinator_context
from sports_aggregator.cfb.player_injury_display import install_player_injury_display


TEAM_HERO_ANCHOR = '        <div class="hero-context" aria-label="Program identity">\n'
TEAM_HERO_INSERT = (
    '        {{ coordinator_team_summary(team.team_id, season) }}\n'
    '        <div class="hero-context" aria-label="Program identity">\n'
)
TEAM_FACTS_ANCHOR = '    <div class="fact"><b>{{ movements.counts.get(\'DRAFTED\', 0) }}</b><span>Drafted departures</span></div>\n</div>'
TEAM_FACTS_INSERT = (
    '    <div class="fact"><b>{{ movements.counts.get(\'DRAFTED\', 0) }}</b><span>Drafted departures</span></div>\n'
    '    {{ coordinator_continuity_fact(team.team_id, season) }}\n'
    '</div>'
)
TEAM_FACTS_CLASS_ANCHOR = '<div class="facts">'
TEAM_FACTS_CLASS_INSERT = '<div class="facts team-facts">'
TEAM_STYLE_ANCHOR = '\n</style>'
TEAM_STYLE_INSERT = '''
    /* Seven team-summary cards stay on one desktop row. Shared fact rows elsewhere
       retain the six-column site default. */
    @media (min-width: 860px) {
        .team-facts { grid-template-columns: repeat(7, minmax(0, 1fr)); }
        .team-facts .fact { padding-inline: 8px; min-width: 0; }
        .team-facts .fact b { font-size: 1.05rem; }
        .team-facts .fact span { font-size: .54rem; line-height: 1.2; }
    }

</style>'''


class _CoordinatorTemplateLoader(BaseLoader):
    def __init__(self, wrapped: BaseLoader):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_team.html":
            if "coordinator_team_summary(" not in source:
                source = source.replace(TEAM_HERO_ANCHOR, TEAM_HERO_INSERT, 1)
            if "coordinator_continuity_fact(" not in source:
                source = source.replace(TEAM_FACTS_ANCHOR, TEAM_FACTS_INSERT, 1)
            if 'class="facts team-facts"' not in source:
                source = source.replace(TEAM_FACTS_CLASS_ANCHOR, TEAM_FACTS_CLASS_INSERT, 1)
            if ".team-facts" not in source:
                source = source.replace(TEAM_STYLE_ANCHOR, TEAM_STYLE_INSERT, 1)
        return source, filename, uptodate

    def list_templates(self):
        if hasattr(self.wrapped, "list_templates"):
            return self.wrapped.list_templates()
        return []


def _text(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value))


def _number(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return _text(value, fallback)


def _performance(item: dict[str, Any], scope: str) -> tuple[str, str]:
    performance = item.get(
        "career_performance" if scope == "career" else "program_performance"
    ) or {}
    return (
        _number(performance.get("points_per_game")),
        _number(performance.get("yards_per_game")),
    )


def _summary_line(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    career_ppg, career_ypg = _performance(item, "career")
    team_ppg, team_ypg = _performance(item, "program")
    return (
        f"<span class='item'><span class='label'>{_text(item.get('role'))}</span>"
        f"<strong>{_text(item.get('coach_name'))}</strong>"
        f"<span>{team_ppg} PPG · {team_ypg} YPG "
        f"(<span title='Career PPG and YPG'>{career_ppg} · {career_ypg}</span>)</span></span>"
    )


def _team_summary(repository, team_id: int, season: int) -> Markup:
    context = coordinator_context(repository, int(team_id), int(season))
    lines = [
        _summary_line(context.get("offense")),
        _summary_line(context.get("defense")),
    ]
    lines = [line for line in lines if line]
    if not lines:
        return Markup("")
    return Markup(
        '<div class="hero-context" aria-label="Coordinator performance">'
        + "".join(lines)
        + "</div>"
    )


def _continuity_fact(repository, team_id: int, season: int) -> Markup:
    context = coordinator_context(repository, int(team_id), int(season))
    score = context.get("continuity_score")
    value = "—" if score is None else f"{int(score)}/8"
    return Markup(
        f'<div class="fact"><b>{escape(value)}</b><span>Staff continuity</span></div>'
    )


def install_coordinator_display(app) -> None:
    """Register compact team-page coordinator presentation."""
    if app.extensions.get("coordinator_display_installed"):
        install_player_injury_display(app)
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["coordinator_team_summary"] = (
        lambda team_id, season: _team_summary(repository, int(team_id), int(season))
    )
    app.jinja_env.globals["coordinator_continuity_fact"] = (
        lambda team_id, season: _continuity_fact(repository, int(team_id), int(season))
    )
    app.jinja_loader = _CoordinatorTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["coordinator_display_installed"] = True
    install_player_injury_display(app)
