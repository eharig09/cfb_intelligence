"""Visible coordinator continuity panels for team and matchup pages.

The existing templates are large and actively evolving. Rather than duplicating
or replacing them, this module wraps Flask's Jinja loader and inserts small,
stable panel calls at known template anchors. All displayed values come from the
stored coordinator and game tables; no network request occurs during page render.
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


TEAM_ANCHOR = '<div class="layout">\n    <div>\n'
TEAM_INSERT = (
    '<div class="layout">\n    <div>\n'
    '        {{ coordinator_team_panel(team.team_id, season) }}\n'
)
TEAM_HERO_ANCHOR = '        <div class="hero-context" aria-label="Program identity">\n'
TEAM_HERO_INSERT = (
    '        {{ coordinator_team_summary(team.team_id, season) }}\n'
    '        <div class="hero-context" aria-label="Program identity">\n'
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
            if "coordinator_team_panel(" not in source:
                source = source.replace(TEAM_ANCHOR, TEAM_INSERT, 1)
            if "coordinator_team_summary(" not in source:
                source = source.replace(TEAM_HERO_ANCHOR, TEAM_HERO_INSERT, 1)
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
    by_season: dict[int, dict[str, str]] = {}
    for row in rows:
        by_season.setdefault(int(row["season"]), {})[str(row["side"])] = str(row["coach_name"])

    changes = {"offense": 0, "defense": 0}
    for side in changes:
        side_rows = sorted(
            (row for row in rows if row["side"] == side),
            key=lambda row: int(row["season"]),
        )
        for previous, current in zip(side_rows, side_rows[1:]):
            if int(current["season"]) != int(previous["season"]) + 1:
                continue
            if current["coach_name"] != previous["coach_name"]:
                changes[side] += 1

    return {
        "rows": rows,
        "seasons": seasons,
        "by_season": by_season,
        "coverage_start": seasons[0] if seasons else None,
        "coverage_end": seasons[-1] if seasons else None,
        "season_count": len(seasons),
        "changes": changes,
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


def _previous_stop(item: dict[str, Any] | None) -> str:
    stop = (item or {}).get("previous_stop") or {}
    if not stop:
        return "—"
    return f"{_text(stop.get('team'))} ({_text(stop.get('season'))})"


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


def _team_panel(repository, team_id: int, season: int) -> Markup:
    context = coordinator_context(repository, int(team_id), int(season))
    history = _history(repository, int(team_id), int(season))
    offense, defense = context.get("offense"), context.get("defense")

    score = context.get("continuity_score")
    changes = context.get("change_count")
    score_text = "—" if score is None else f"{int(score)}/8"
    change_text = "—" if changes is None else str(int(changes))

    rows = []
    for label, item in (("Offense", offense), ("Defense", defense)):
        if item:
            previous = item.get("previous_coordinator") or "—"
            tenure = f"{item.get('tenure_years', 1)} yr"
            if int(item.get("tenure_years") or 1) != 1:
                tenure += "s"
            source = item.get("source_name") or "Stored source"
            rows.append(
                f"<tr><th>{label}</th><td><strong>{_text(item.get('coach_name'))}</strong>"
                f"<span class='sub'>{_text(source)}</span></td><td>{_text(tenure)}</td>"
                f"<td>{_text(_side_label(item))}</td><td>{_text(previous)}</td>"
                f"<td>{_previous_stop(item)}</td></tr>"
            )
        else:
            rows.append(
                f"<tr><th>{label}</th><td colspan='5' class='is-empty'>"
                f"No {season} coordinator row is stored.</td></tr>"
            )

    recent = []
    for year in sorted(history["by_season"], reverse=True)[:6]:
        staff = history["by_season"][year]
        recent.append(
            f"<tr><th>{year}</th><td>{_text(staff.get('offense'))}</td>"
            f"<td>{_text(staff.get('defense'))}</td></tr>"
        )

    note = (
        "Tenure and change labels use consecutive stored seasons only. "
        "Performance averages shown above use completed games from stored coordinator seasons."
    )
    html = f"""
<section class="section coordinator-panel" data-mobile-tab-panel="overview">
  <h2>Coaching continuity</h2>
  <div class="section-note">{escape(note)}</div>
  <div class="facts coordinator-facts">
    <div class="fact"><b>{score_text}</b><span>Staff continuity</span></div>
    <div class="fact"><b>{change_text}</b><span>Coordinator changes</span></div>
    <div class="fact"><b>{_text(_coverage_label(history))}</b><span>Historical coverage</span></div>
  </div>
  <div class="table-scroll">
    <table class="data dense">
      <thead><tr><th>Unit</th><th>Coordinator</th><th>Tenure</th><th>{season} status</th><th>Previous coordinator</th><th>Previous stop</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <h3 class="subhead">Recent coordinator history</h3>
  <div class="table-scroll">
    <table class="data dense">
      <thead><tr><th>Season</th><th>OC</th><th>DC</th></tr></thead>
      <tbody>{''.join(recent) if recent else '<tr><td colspan="3" class="is-empty">No coordinator history stored.</td></tr>'}</tbody>
    </table>
  </div>
</section>
"""
    return Markup(html)


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
  <div class="content-meta">Parenthetical/career values use stored coordinator seasons · OC = scored · DC = allowed · stored history: {_text(_coverage_label(history))}</div>
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
    app.jinja_env.globals["coordinator_team_panel"] = (
        lambda team_id, season: _team_panel(repository, int(team_id), int(season))
    )
    app.jinja_env.globals["coordinator_matchup_panel"] = (
        lambda away_id, home_id, away, home, season: _matchup_panel(
            repository, int(away_id), int(home_id), str(away), str(home), int(season)
        )
    )
    app.jinja_loader = _CoordinatorTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["coordinator_display_installed"] = True
