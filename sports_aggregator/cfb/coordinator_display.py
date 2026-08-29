"""Compact coordinator context for team and matchup pages."""

from __future__ import annotations

from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.coordinator_balance import coordinator_run_pass_context
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
GAME_BALANCE_ANCHOR = '<section class="section" data-mobile-tab-panel="overview">\n    <h2>Players with history against this opponent</h2>'
GAME_BALANCE_INSERT = (
    '{{ coordinator_matchup_balance(game.away_team_id, game.home_team_id, game.season) }}\n\n'
    '<section class="section" data-mobile-tab-panel="overview">\n'
    '    <h2>Players with history against this opponent</h2>'
)
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
        elif template == "cfb_game.html" and "coordinator_matchup_balance(" not in source:
            source = source.replace(GAME_BALANCE_ANCHOR, GAME_BALANCE_INSERT, 1)
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


def _balance_inline(balance: dict[str, Any] | None) -> str:
    if not balance:
        return ""
    basis = balance.get("program") or balance.get("career")
    if not basis:
        return ""
    return f" · R/P {basis['run_pct']:.0f}/{basis['pass_pct']:.0f}"


def _summary_line(item: dict[str, Any] | None, balance: dict[str, Any] | None = None) -> str:
    if not item:
        return ""
    career_ppg, career_ypg = _performance(item, "career")
    team_ppg, team_ypg = _performance(item, "program")
    return (
        f"<span class='item'><span class='label'>{_text(item.get('role'))}</span>"
        f"<strong>{_text(item.get('coach_name'))}</strong>"
        f"<span>{team_ppg} PPG · {team_ypg} YPG "
        f"(<span title='Career PPG and YPG'>{career_ppg} · {career_ypg}</span>)"
        f"{_balance_inline(balance)}</span></span>"
    )


def _team_summary(repository, team_id: int, season: int) -> Markup:
    context = coordinator_context(repository, int(team_id), int(season))
    offense_balance = coordinator_run_pass_context(repository, int(team_id), int(season))
    lines = [
        _summary_line(context.get("offense"), offense_balance),
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


def _balance_card(repository, team_id: int) -> str:
    context = coordinator_run_pass_context(repository, int(team_id), _balance_card.season)
    if not context:
        return ""
    career = context.get("career")
    program = context.get("program")
    headline = program or career
    if not headline:
        return ""
    rows = []
    for split in context.get("season_splits", [])[:5]:
        rows.append(
            f"<span>{int(split['season'])} {escape(split['team'])}: "
            f"{split['run_pct']:.0f}/{split['pass_pct']:.0f}</span>"
        )
    program_text = (
        f"At {escape(context['team'])}: {program['run_pct']:.0f}% run / {program['pass_pct']:.0f}% pass"
        if program else "No program split stored yet"
    )
    career_text = (
        f"Career assignments: {career['run_pct']:.0f}% run / {career['pass_pct']:.0f}% pass"
        if career else ""
    )
    return (
        '<article class="situation-card">'
        f"<h3>{escape(context['team'])} · {escape(context['coach_name'])}</h3>"
        f"<p><strong>{headline['run_pct']:.0f}/{headline['pass_pct']:.0f}</strong> observed run/pass balance</p>"
        f"<div class='meta'>{program_text}{' · ' if career_text and program_text else ''}{career_text}</div>"
        f"<div class='meta' style='display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:6px'>{''.join(rows)}</div>"
        '</article>'
    )


def _matchup_balance(repository, away_team_id: int, home_team_id: int, season: int) -> Markup:
    # Keep the helper signature compact while letting the individual card helper
    # reuse the same renderer.
    _balance_card.season = int(season)
    cards = [
        _balance_card(repository, int(away_team_id)),
        _balance_card(repository, int(home_team_id)),
    ]
    cards = [card for card in cards if card]
    if not cards:
        return Markup("")
    return Markup(
        '<section class="section" data-mobile-tab-panel="overview">'
        '<h2>Offensive coordinator tendencies</h2>'
        '<div class="section-note">Observed team rushing and passing attempts from seasons assigned to each current OC. '
        'These describe the offenses they coordinated; they do not prove who called each play.</div>'
        '<div class="split">' + "".join(cards) + '</div></section>'
    )


def install_coordinator_display(app) -> None:
    """Register compact team and matchup coordinator presentation."""
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
    app.jinja_env.globals["coordinator_matchup_balance"] = (
        lambda away_team_id, home_team_id, season: _matchup_balance(
            repository, int(away_team_id), int(home_team_id), int(season))
    )
    app.jinja_loader = _CoordinatorTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["coordinator_display_installed"] = True
    install_player_injury_display(app)
