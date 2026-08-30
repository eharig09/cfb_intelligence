"""Render precomputed quarterback air-yard summaries in postgame reports."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.qb_air_yards import game_summary

ANCHOR = "{{ postgame_tendencies(game) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_qb_air_yards(game) }}"

STYLE = '''<style>
.pg-qb-air{margin:0 0 18px}.pg-qb-air-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-end;margin:17px 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}.pg-qb-air-head h3{margin:0;font-size:.8rem}.pg-qb-air-head span{font-size:.51rem;color:var(--muted)}
.pg-qb-air-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.pg-qb-card{border-top:2px solid var(--line);padding-top:9px;min-width:0}.pg-qb-card h4{margin:0 0 7px;font-size:.72rem}.pg-qb-card h4 a{text-decoration:none}.pg-qb-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.pg-qb-metric span{display:block;font-size:.45rem;text-transform:uppercase;letter-spacing:.055em;color:var(--muted);font-weight:800}.pg-qb-metric strong{display:block;margin-top:2px;font-family:var(--display-font);font-size:.75rem;font-variant-numeric:tabular-nums}.pg-qb-depth{margin-top:8px;padding-top:7px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:5px 10px;font-size:.53rem}.pg-qb-depth strong{font-variant-numeric:tabular-nums}.pg-qb-note{margin:7px 0 0;color:var(--muted);font-size:.52rem;line-height:1.45}
@media(max-width:760px){.pg-qb-air-grid{grid-template-columns:1fr}.pg-qb-air-head span{display:none}}@media(max-width:480px){.pg-qb-metrics{grid-template-columns:1fr 1fr}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_qb_air_yards(" not in source and ANCHOR in source:
            source = source.replace(ANCHOR, REPLACEMENT, 1)
        return source, filename, uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped, "list_templates") else []


def _f1(value: Any, *, signed: bool = False) -> str:
    if value is None: return "—"
    try:
        number = float(value)
        return f"{number:+.1f}" if signed else f"{number:.1f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    if value is None: return "—"
    try: return f"{100 * float(value):.0f}%"
    except (TypeError, ValueError): return "—"


def _card(row: dict[str, Any]) -> str:
    name = escape(str(row.get("player_name") or "Quarterback"))
    player_id = row.get("player_id")
    href = url_for("cfb.player_preview", player_id=player_id, season=row.get("season")) if player_id else None
    shown = f'<a href="{href}">{name}</a>' if href else name
    metrics = (
        ("Air yards", _f1(row.get("measured_air_yards"))),
        ("Air yds / comp", _f1(row.get("measured_adot"))),
        ("YAC", _f1(row.get("yards_after_catch"))),
        ("Pass EPA", _f1(row.get("pass_epa"), signed=True)),
        ("EPA / attr. pass", _f1(row.get("epa_per_attributed_pass"), signed=True)),
        ("Measured comps", str(int(row.get("measured_completions") or 0))),
        ("Attr. pass plays", str(int(row.get("attributed_pass_plays") or 0))),
        ("Numeric coverage", _pct(row.get("numeric_depth_coverage"))),
    )
    metric_html = ''.join(
        f'<div class="pg-qb-metric"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
        for label, value in metrics
    )
    depth = (
        ("Behind LOS", row.get("behind_line_plays")),
        ("Short", row.get("short_plays")),
        ("Intermediate", row.get("intermediate_plays")),
        ("Deep", row.get("deep_plays")),
    )
    depth_html = ' · '.join(f'{escape(label)} <strong>{int(value or 0)}</strong>' for label, value in depth)
    return (
        '<article class="pg-qb-card">'
        f'<h4>{shown} <small>· {escape(str(row.get("team") or ""))}</small></h4>'
        f'<div class="pg-qb-metrics">{metric_html}</div>'
        f'<div class="pg-qb-depth">{depth_html}</div>'
        '</article>'
    )


def _render(repository, game: dict[str, Any]) -> Markup:
    try: rows = game_summary(repository, int(game.get("game_id") or 0))
    except Exception: rows = []
    if not rows: return Markup("")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("team") or "Team")].append(row)
    preferred = [str(game.get("away_team") or ""), str(game.get("home_team") or "")]
    ordered: list[dict[str, Any]] = []
    for team in preferred:
        ordered.extend(grouped.pop(team, []))
    for team_rows in grouped.values():
        ordered.extend(team_rows)
    return Markup(
        STYLE + '<section class="pg-qb-air">'
        '<div class="pg-qb-air-head"><h3>Quarterback air yards</h3><span>qb-air-yards-v1 · play-detail-v3 × ep-v1</span></div>'
        f'<div class="pg-qb-air-grid">{"".join(_card(row) for row in ordered)}</div>'
        '<p class="pg-qb-note">Air yards and YAC are measured only on completions with an unambiguous catch spot. “Air yds / comp” is not full aDOT; numeric coverage shows how much of the attributed passing sample has measured depth.</p>'
        '</section>'
    )


def install_qb_air_yards_display(app) -> None:
    if app.extensions.get("qb_air_yards_display_installed"): return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_qb_air_yards"] = lambda game: _render(repository, dict(game))
    app.jinja_loader = _Loader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["qb_air_yards_display_installed"] = True
