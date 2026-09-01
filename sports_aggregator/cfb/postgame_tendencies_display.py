"""Render high-confidence 2025+ play-text tendency splits in postgame reports."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.qb_air_yards_display import install_qb_air_yards_display
from sports_aggregator.cfb.team_game_tendencies import game_summary, MODEL_VERSION

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_tendencies(game) }}"
MIN_METRIC_SAMPLE = 4

STYLE = ""  # served from static/cfb_analysis.css, not inlined per render

LABELS={"rush_direction":"Rush direction","pass_depth":"Pass depth","pass_location":"Pass location"}
ORDER=("rush_direction","pass_depth","pass_location")
CANONICAL_VALUES={"rush_direction":("left","middle","right"),"pass_depth":("behind_line","short","intermediate","deep"),"pass_location":("left","middle","right")}


class _Loader(BaseLoader):
    def __init__(self,wrapped): self.wrapped=wrapped
    def get_source(self,environment,template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source,filename,uptodate=self.wrapped.get_source(environment,template)
        if template=="cfb_box_score.html" and "postgame_tendencies(" not in source and ANCHOR in source: source=source.replace(ANCHOR,REPLACEMENT,1)
        return source,filename,uptodate
    def list_templates(self): return self.wrapped.list_templates() if hasattr(self.wrapped,"list_templates") else []


def _f2(value:Any)->str:
    try:return f"{float(value):+.2f}"
    except (TypeError,ValueError):return "—"

def _pct(value:Any)->str:
    try:return f"{100*float(value):.0f}%"
    except (TypeError,ValueError):return "—"


def _dimension_html(dimension:str,rows:list[dict[str,Any]])->str:
    if not rows:return ""
    coverage=max((float(row.get("coverage") or 0.0) for row in rows),default=0.0); by_value={str(row.get("value") or ""):row for row in rows}; body=[]
    for value in CANONICAL_VALUES[dimension]:
        row=by_value.get(value); plays=int((row or {}).get("plays") or 0); enough=plays>=MIN_METRIC_SAMPLE; css="pg-tendency-row"+(" low-sample" if not enough else "")
        body.append(f'<div class="{css}"><span class="value">{escape(value.replace("_"," "))}</span><span class="num">{plays}</span><span class="num epa">{_f2(row.get("epa_per_play")) if row and enough else "—"}</span><span class="num">{_pct(row.get("success_rate")) if row and enough else "—"}</span></div>')
    return '<section class="pg-tendency-block">'+f'<div class="pg-tendency-label"><strong>{escape(LABELS[dimension])}</strong><span>{_pct(coverage)} classified</span></div><div class="pg-tendency-row header"><span>Split</span><span class="num">Plays</span><span class="num">EPA/play</span><span class="num">Success</span></div>'+''.join(body)+'</section>'


def _render(repository,game:dict[str,Any])->Markup:
    try:rows=game_summary(repository,int(game.get("game_id") or 0),min_plays=1,min_coverage=.30)
    except Exception:rows=[]
    if not rows:return Markup("")
    grouped=defaultdict(lambda:defaultdict(list))
    for row in rows:
        dimension=str(row.get("dimension") or "")
        if dimension in LABELS: grouped[str(row.get("team") or "Team")][dimension].append(row)
    if not grouped:return Markup("")
    preferred=[str(game.get("away_team") or ""),str(game.get("home_team") or "")]; teams=[team for team in preferred if team in grouped]; teams.extend(team for team in grouped if team not in teams); cards=[]
    for team in teams:
        dimensions=''.join(_dimension_html(d,grouped[team].get(d,[])) for d in ORDER if grouped[team].get(d))
        if dimensions:cards.append(f'<article class="pg-tendency-team"><h4>{escape(team)}</h4>{dimensions}</article>')
    if not cards:return Markup("")
    return Markup(STYLE+'<section class="pg-tendency">'+f'<div class="pg-tendency-head"><h3>Play tendencies</h3><span>play-detail-v3 × {escape(MODEL_VERSION)} · evidence thresholds applied</span></div>'+f'<div class="pg-tendency-teams">{"".join(cards)}</div>'+'<p class="pg-tendency-note">Pass depth uses measured catch-spot air yards when the provider field-side code resolves cleanly, with lexical depth as fallback. EPA and success are withheld for splits with fewer than 4 classified plays.</p></section>')


def install_postgame_tendencies_display(app)->None:
    if app.extensions.get("postgame_tendencies_display_installed"):return
    repository=app.extensions["cfb_repository"]; app.jinja_env.globals["postgame_tendencies"]=lambda game:_render(repository,dict(game)); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions["postgame_tendencies_display_installed"]=True; install_qb_air_yards_display(app)
