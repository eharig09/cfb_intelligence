"""Render high-confidence 2025+ play-text tendency splits in postgame reports."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.team_game_tendencies import game_summary

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_tendencies(game) }}"

STYLE = '''<style>
.pg-tendency{margin:0 0 18px}.pg-tendency-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-end;margin:17px 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}.pg-tendency-head h3{margin:0;font-size:.8rem}.pg-tendency-head span{font-size:.51rem;color:var(--muted)}
.pg-tendency-teams{display:grid;grid-template-columns:1fr 1fr;gap:10px}.pg-tendency-team{min-width:0}.pg-tendency-team>h4{margin:0 0 6px;font-size:.69rem}
.pg-tendency-block{border-top:1px solid var(--line);margin-top:7px;padding-top:6px}.pg-tendency-block:first-of-type{margin-top:0}.pg-tendency-label{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:3px}.pg-tendency-label strong{font-size:.58rem;text-transform:uppercase;letter-spacing:.055em}.pg-tendency-label span{font-size:.48rem;color:var(--muted)}
.pg-tendency-row{display:grid;grid-template-columns:minmax(72px,1fr) 48px 66px 58px;gap:6px;align-items:center;padding:4px 0;border-top:1px solid color-mix(in srgb,var(--line) 72%,transparent);font-size:.57rem}.pg-tendency-row.header{border-top:0;color:var(--muted);font-size:.47rem;text-transform:uppercase;letter-spacing:.045em;font-weight:800}.pg-tendency-row .num{text-align:right;font-variant-numeric:tabular-nums}.pg-tendency-row .value{font-weight:750;text-transform:capitalize}.pg-tendency-row .epa{font-family:var(--display-font);font-size:.65rem}.pg-tendency-note{margin:7px 0 0;font-size:.53rem;line-height:1.45;color:var(--muted)}
@media(max-width:720px){.pg-tendency-teams{grid-template-columns:1fr}.pg-tendency-head span{display:none}}@media(max-width:430px){.pg-tendency-row{grid-template-columns:minmax(68px,1fr) 42px 60px 52px}}
</style>'''

LABELS = {
    "rush_direction": "Rush direction",
    "pass_depth": "Pass depth",
    "pass_location": "Pass location",
}
ORDER = ("rush_direction", "pass_depth", "pass_location")
VALUE_ORDER = {
    "rush_direction": {"left": 0, "middle": 1, "right": 2},
    "pass_depth": {"short": 0, "deep": 1},
    "pass_location": {"left": 0, "middle": 1, "right": 2},
}


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_tendencies(" not in source and ANCHOR in source:
            source = source.replace(ANCHOR, REPLACEMENT, 1)
        return source, filename, uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped, "list_templates") else []


def _f2(value: Any) -> str:
    try: return f"{float(value):+.2f}"
    except (TypeError, ValueError): return "—"


def _pct(value: Any) -> str:
    try: return f"{100 * float(value):.0f}%"
    except (TypeError, ValueError): return "—"


def _dimension_html(dimension: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    coverage = max((float(row.get("coverage") or 0.0) for row in rows), default=0.0)
    rows = sorted(rows, key=lambda r: VALUE_ORDER.get(dimension, {}).get(str(r.get("value")), 99))
    body = []
    for row in rows:
        body.append(
            '<div class="pg-tendency-row">'
            f'<span class="value">{escape(str(row.get("value") or "—").replace("_", " "))}</span>'
            f'<span class="num">{int(row.get("plays") or 0)}</span>'
            f'<span class="num epa">{_f2(row.get("epa_per_play"))}</span>'
            f'<span class="num">{_pct(row.get("success_rate"))}</span>'
            '</div>'
        )
    return (
        '<section class="pg-tendency-block">'
        f'<div class="pg-tendency-label"><strong>{escape(LABELS[dimension])}</strong><span>{_pct(coverage)} classified</span></div>'
        '<div class="pg-tendency-row header"><span>Split</span><span class="num">Plays</span><span class="num">EPA/play</span><span class="num">Success</span></div>'
        + ''.join(body) + '</section>'
    )


def _render(repository, game: dict[str, Any]) -> Markup:
    try:
        rows = game_summary(repository, int(game.get("game_id") or 0), min_plays=4, min_coverage=0.30)
    except Exception:
        rows = []
    if not rows:
        return Markup("")

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        dimension = str(row.get("dimension") or "")
        if dimension not in LABELS:
            continue
        grouped[str(row.get("team") or "Team")][dimension].append(row)
    if not grouped:
        return Markup("")

    preferred = [str(game.get("away_team") or ""), str(game.get("home_team") or "")]
    teams = [team for team in preferred if team in grouped]
    teams.extend(team for team in grouped if team not in teams)
    cards = []
    for team in teams:
        dimensions = ''.join(_dimension_html(d, grouped[team].get(d, [])) for d in ORDER)
        if dimensions:
            cards.append(f'<article class="pg-tendency-team"><h4>{escape(team)}</h4>{dimensions}</article>')
    if not cards:
        return Markup("")

    return Markup(
        STYLE + '<section class="pg-tendency">'
        '<div class="pg-tendency-head"><h3>Play tendencies</h3><span>play-detail-v1 × ep-v1 · evidence thresholds applied</span></div>'
        f'<div class="pg-tendency-teams">{"".join(cards)}</div>'
        '<p class="pg-tendency-note">Only splits with at least 4 classified plays and at least 30% game-level classification coverage are shown. Older sparse-description seasons are intentionally suppressed.</p>'
        '</section>'
    )


def install_postgame_tendencies_display(app) -> None:
    if app.extensions.get("postgame_tendencies_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_tendencies"] = lambda game: _render(repository, dict(game))
    app.jinja_loader = _Loader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["postgame_tendencies_display_installed"] = True
