"""Compact coordinator context for team and matchup pages.

Team pages show coordinator performance beside the head coach and a single
continuity fact card. Matchup pages retain the side-by-side staff comparison.
All values come from stored coordinator/game data; page rendering does not make
network requests.
"""

from __future__ import annotations

from contextlib import closing
from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.coordinator_context import (
    coordinator_context,
    coordinator_matchup_context,
)
from sports_aggregator.cfb.coordinators import initialize


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
GAME_ANCHOR = '<div class="scoreboard">'
GAME_INSERT = (
    '{{ coordinator_matchup_panel(game.away_team_id, game.home_team_id, '
    'game.away_team, game.home_team, game.season) }}\n\n<div class="scoreboard">'
)


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
        elif template == "cfb_game.html" and "coordinator_matchup_panel(" not in source:
            source = source.replace(GAME_ANCHOR, GAME_INSERT, 1)
        return source, filename, uptodate

    def list_templates(self):
        if hasattr(self.wrapped, "list_templates"):
            return self.wrapped.list_templates()
        return []


def _history(repository, team_id: int, season: int) -> dict[str, Any]:
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT season,side,role,coach_name,source_name,source_url
               FROM coordinator_seasons
               WHERE team_id=? AND season<=?
               ORDER BY season DESC, side""",
            (int(team_id), int(season)),
        ).fetchall()]
    seasons = sorted({int(row["season"]) for row in rows})
    return {
        "coverage_start": seasons[0] if seasons else None,
        "coverage_end": seasons[-1] if seasons else None,
        "season_count": len(seasons),
    }


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


def _side_label(item: dict[str, Any] | None) -> str:
    if not item:
        return "No current data"
    changed = item.get("changed")
    if changed is True:
        return "New this season"
    if changed is False:
        return item.get("continuity_label") or "Returning"
    return "History needed"


def _coverage_label(history: dict[str, Any]) -> str:
    count = int(history.get("season_count") or 0)
    start, end = history.get("coverage_start"), history.get("coverage_end")
    if not count:
        return "No stored history"
    if count == 1:
        return f"{start} · 1 season"
    return f"{start}–{end} · {count} seasons"


def _performance_cells(item: dict[str, Any], scope: str = "career") -> tuple[str, str, str]:
    performance = item.get("career_performance" if scope == "career" else "program_performance") or {}
    points = _number(performance.get("points_per_game"))
    yards = _number(performance.get("yards_per_game"))
    games = int(performance.get("games") or 0)
    yard_games = int(performance.get("yard_games") or 0)
    coverage = f"{games} games"
    if yard_games != games and yard_games:
        coverage += f" · yards {yard_games}"
    elif not yard_games:
        coverage += " · yards unavailable"
    return points, yards, coverage


def _summary_line(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    career_ppg, career_ypg, _ = _performance_cells(item, "career")
    team_ppg, team_ypg, _ = _performance_cells(item, "program")
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


def _matchup_team_card(name: str, packet: dict[str, Any], history: dict[str, Any]) -> str:
    score = packet.get("continuity_score")
    changes = packet.get("change_count")
    score_text = "—" if score is None else f"{int(score)}/8"
    changes_text = "—" if changes is None else str(int(changes))
    staff_rows = []
    for label, item in (("OC", packet.get("offense")), ("DC", packet.get("defense"))):
        if item:
            tenure = int(item.get("tenure_years") or 1)
            career_ppg, career_ypg, coverage = _performance_cells(item, "career")
            team_ppg, team_ypg, _ = _performance_cells(item, "program")
            staff_rows.append(
                f"<tr><th>{label}</th><td><strong>{_text(item.get('coach_name'))}</strong>"
                f"<span class='sub'>{_text(_side_label(item))}</span></td>"
                f"<td>{tenure} yr{'s' if tenure != 1 else ''}</td>"
                f"<td><strong>{team_ppg} · {team_ypg}</strong>"
                f"<span class='sub'>career {career_ppg} · {career_ypg} · {coverage}</span></td></tr>"
            )
        else:
            staff_rows.append(f"<tr><th>{label}</th><td colspan='3'>No {packet.get('season')} row</td></tr>")
    return f"""
<div class="ats-card coordinator-card">
  <h4>{_text(name)}</h4>
  <div class="matchup-summary">
    <span><b>{score_text}</b> continuity</span>
    <span><b>{changes_text}</b> changes</span>
  </div>
  <table class="ats-grid">
    <thead><tr><th></th><th>Coordinator</th><th>Tenure</th><th>At team PPG · YPG</th></tr></thead>
    <tbody>{''.join(staff_rows)}</tbody>
  </table>
  <div class="content-meta">Career values use stored coordinator seasons · OC = scored · DC = allowed · stored history: {_text(_coverage_label(history))}</div>
</div>
"""


def _matchup_panel(repository, away_team_id: int, home_team_id: int,
                   away_name: str, home_name: str, season: int) -> Markup:
    packet = coordinator_matchup_context(
        repository, int(away_team_id), int(home_team_id), int(season)
    )
    away_history = _history(repository, int(away_team_id), int(season))
    home_history = _history(repository, int(home_team_id), int(season))
    edge = packet.get("continuity_edge")
    edge_text = (
        f"{away_name} has the stronger stored staff-continuity profile."
        if edge == "away" else
        f"{home_name} has the stronger stored staff-continuity profile."
        if edge == "home" else
        "Stored staff continuity is even."
        if edge == "even" else
        "More historical coordinator data is needed for a continuity comparison."
    )
    html = f"""
<section class="section coordinator-matchup" aria-label="Coaching continuity comparison">
  <h2>Coaching continuity</h2>
  <div class="section-note">{escape(edge_text)} Career averages use only completed games in stored coordinator seasons.</div>
  <div class="ats-pair">
    {_matchup_team_card(away_name, packet['away'], away_history)}
    {_matchup_team_card(home_name, packet['home'], home_history)}
  </div>
</section>
"""
    return Markup(html)


def install_coordinator_display(app) -> None:
    """Register stored-data helpers and inject their calls into CFB templates."""
    if app.extensions.get("coordinator_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["coordinator_team_summary"] = (
        lambda team_id, season: _team_summary(repository, int(team_id), int(season))
    )
    app.jinja_env.globals["coordinator_continuity_fact"] = (
        lambda team_id, season: _continuity_fact(repository, int(team_id), int(season))
    )
    app.jinja_env.globals["coordinator_matchup_panel"] = (
        lambda away_id, home_id, away, home, season: _matchup_panel(
            repository, int(away_id), int(home_id), str(away), str(home), int(season)
        )
    )
    app.jinja_loader = _CoordinatorTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["coordinator_display_installed"] = True
