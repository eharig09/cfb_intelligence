"""Compact coordinator context for team and matchup pages."""

from __future__ import annotations

from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.coordinator_balance import (
    HISTORY_SEASONS, coordinator_run_pass_context, team_run_pass_context,
)
from sports_aggregator.cfb.coordinator_pace import (
    RECENT_SEASONS, coordinator_pace, team_pace,
)
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
# `.team-facts` (seven cards on one desktop row) is styled in static/cfb.css.
# It used to be spliced in here as a page-local <style> block, which quietly
# stopped applying once the CFB templates moved their styles to that sheet.


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


def _performance(item: dict[str, Any], scope: str) -> tuple[Any, Any]:
    performance = item.get(
        "career_performance" if scope == "career" else "program_performance"
    ) or {}
    return performance.get("points_per_game"), performance.get("yards_per_game")


def _balance_inline(balance: dict[str, Any] | None) -> str:
    if not balance:
        return ""
    basis = balance.get("program") or balance.get("career")
    if not basis:
        return ""
    return f" · R/P {basis['run_pct']:.0f}/{basis['pass_pct']:.0f}"


def _summary_line(item: dict[str, Any] | None, balance: dict[str, Any] | None = None) -> str:
    """One coordinator: what his offence has done here, and what it has done.

    A coordinator in his first season at a school has no record at it, and
    leading with two dashes and hiding the career behind them read as no data
    at all -- "— PPG · — YPG (24.0 · 348.6)". Where there is nothing at the
    school yet, the career is the line, and it says so.
    """
    if not item:
        return ""
    career_ppg, career_ypg = _performance(item, "career")
    team_ppg, team_ypg = _performance(item, "program")
    here = team_ppg is not None or team_ypg is not None
    anywhere = career_ppg is not None or career_ypg is not None
    if not here and not anywhere:
        # A coordinator arriving from outside college football has no record
        # to show, and "career — PPG · — YPG" claims one.
        numbers = "<span class='muted'>no stored record</span>"
    elif not here:
        numbers = (f"<span title='Across every stop he has held'>career "
                   f"{_number(career_ppg)} PPG · {_number(career_ypg)} YPG</span>")
    elif (team_ppg, team_ypg) == (career_ppg, career_ypg):
        # His first stop: the career and the school are the same games.
        numbers = f"{_number(team_ppg)} PPG · {_number(team_ypg)} YPG"
    else:
        numbers = (f"{_number(team_ppg)} PPG · {_number(team_ypg)} YPG "
                   f"(<span title='Across every stop he has held'>career "
                   f"{_number(career_ppg)} · {_number(career_ypg)}</span>)")
    return (
        f"<span class='item'><span class='label'>{_text(item.get('role'))}</span>"
        f"<strong>{_text(item.get('coach_name'))}</strong>"
        f"<span>{numbers}{_balance_inline(balance)}</span></span>"
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


def _pace_line(packet: dict | None, label: str) -> str:
    """Tempo in the two units people actually argue about."""
    if not packet:
        return ""
    parts = []
    if packet.get("seconds_per_play") is not None:
        parts.append(f"{packet['seconds_per_play']:.1f}s per play")
    if packet.get("plays_per_game") is not None:
        parts.append(f"{packet['plays_per_game']:.0f} plays/game")
    if not parts:
        return ""
    return (f"<span><b>{label}</b> " + " · ".join(parts)
            + f" <i>({packet['games']} games)</i></span>")


def _pace_for(repository, team_id: int, season: int,
              coach_name: str | None) -> tuple[str, str]:
    """Pace for the coordinator when he is known, for the offence when he is not.

    `coordinator_seasons` is filled by a command no refresh profile runs, so on
    most databases there is no name to attribute an offence to. The tempo is a
    property of the offence either way; what changes is whose it can honestly
    be called.
    """
    team = (repository.get_team(int(team_id)) or {}).get("school")
    if coach_name:
        packet = coordinator_pace(repository, coach_name, through_season=int(season))
        if packet and (packet.get("career") or packet.get("recent")):
            recent, whole = packet.get("recent"), packet.get("career")
            # Play-by-play starts well after `team_stats` does, so a career
            # older than the plays is measured only over the part that has
            # them -- and when that is the whole of it, one line says it.
            same = (recent and whole and recent["games"] == whole["games"])
            return (_pace_line(recent, f"Since {packet['recent_from']}")
                    + ("" if same else _pace_line(whole, "Career")),
                    "under this coordinator")
    if not team:
        return "", ""
    seasons = [int(season) - offset for offset in range(1, RECENT_SEASONS + 1)]
    return (_pace_line(team_pace(repository, team, seasons),
                       f"{min(seasons)}–{max(seasons)}"),
            "this offence, coordinator not on record")


def _balance_card(repository, team_id: int, season: int) -> str:
    """Run/pass balance and tempo for one offence.

    The coordinator reading is preferred and the programme's is the fallback,
    rather than the coordinator reading being the only one. `coordinator_seasons`
    is filled by a command no refresh profile runs, so on most databases it is
    empty, `coordinator_run_pass_context` returns None for every team, both
    cards came back blank and the whole section vanished from the page. The
    tendency never needed a coordinator: `team_stats` carries the attempts from
    2015, which is where both readings get it from anyway.
    """
    team = (repository.get_team(int(team_id)) or {}).get("school")
    context = coordinator_run_pass_context(repository, int(team_id), int(season))
    if not context and team:
        context = team_run_pass_context(repository, team, int(season))
    if not context:
        return ""
    career = context.get("career")
    program = context.get("program")
    headline = context.get("current") or program or career
    if not headline:
        return ""
    coach = context.get("coach_name")
    rows = [
        f"<span>{int(split['season'])} "
        # The team is only worth naming when it can change between rows, which
        # it can for a coordinator and cannot for a programme.
        + (f"{escape(split['team'])} " if coach else "")
        + f"{split['run_pct']:.0f}/{split['pass_pct']:.0f}</span>"
        for split in context.get("season_splits", [])[:HISTORY_SEASONS]
    ]
    seasons = program["seasons"] if program else 0
    program_text = (
        f"{escape(context['team'])} over {seasons} season{'s' if seasons != 1 else ''}: "
        f"{program['run_pct']:.0f}% run / {program['pass_pct']:.0f}% pass"
        if program else ""
    )
    # Saying it twice is not saying it louder: a coordinator with one stop has
    # a career identical to his programme's.
    career_text = (
        f"Career assignments: {career['run_pct']:.0f}% run / {career['pass_pct']:.0f}% pass"
        if career and (not program or career["seasons"] != program["seasons"]) else ""
    )
    pace, basis = _pace_for(repository, team_id, season, coach)
    title = escape(context["team"]) + (f" · {escape(coach)}" if coach else "")
    return (
        '<article class="situation-card">'
        f"<h3>{title}</h3>"
        f"<p><strong>{headline['run_pct']:.0f}/{headline['pass_pct']:.0f}</strong> "
        f"run/pass, {int(headline['season']) if headline.get('season') else season}</p>"
        + (f"<div class='meta pace-lines'>{pace}</div>" if pace else "")
        + f"<div class='meta'>{program_text}{' · ' if career_text and program_text else ''}{career_text}</div>"
        + f"<div class='meta pace-lines'>{''.join(rows)}</div>"
        + (f"<div class='meta'>{escape(basis)}</div>" if basis and not coach else "")
        + '</article>'
    )


def _matchup_balance(repository, away_team_id: int, home_team_id: int, season: int) -> Markup:
    cards = [
        _balance_card(repository, int(away_team_id), int(season)),
        _balance_card(repository, int(home_team_id), int(season)),
    ]
    cards = [card for card in cards if card]
    if not cards:
        return Markup("")
    return Markup(
        '<section class="section" data-mobile-tab-panel="overview">'
        '<h2>Offensive tempo and balance</h2>'
        '<div class="section-note">Snap-to-snap tempo within a drive, so the other '
        'side&rsquo;s possession does not count. Describes the offence, not who '
        'called the play.</div>'
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
