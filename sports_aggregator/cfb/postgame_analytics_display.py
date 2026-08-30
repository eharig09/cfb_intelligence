"""Supplement the postgame report with pace/game-state and WP turning points."""
from __future__ import annotations

from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.wp_turning_points import game_turning_points

ANCHOR="{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT=ANCHOR+"\n{{ postgame_pace_and_leverage(game) }}"
WP_MODEL_VERSION="wp-v2"

STYLE='''<style>
.pg-analytics{margin:0 0 26px}.pg-analytics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin:10px 0 18px}
.pg-analytics-card{border:1px solid var(--line);background:var(--paper);padding:11px 12px}.pg-analytics-card h4{margin:0 0 8px;font-size:.76rem}.pg-row{display:grid;grid-template-columns:1.4fr .8fr .8fr;gap:8px;padding:4px 0;border-top:1px solid var(--line);font-size:.66rem}.pg-row:first-of-type{border-top:0}.pg-num{text-align:right;font-variant-numeric:tabular-nums}.pg-note{color:var(--muted);font-size:.61rem;line-height:1.4}.pg-turn{border-left:3px solid var(--line);padding:8px 10px;margin:7px 0}.pg-turn strong{display:block;font-size:.7rem}.pg-turn p{margin:3px 0 0;font-size:.65rem;line-height:1.35}
</style>'''


class _Loader(BaseLoader):
    def __init__(self,wrapped): self.wrapped=wrapped
    def get_source(self,environment,template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source,filename,uptodate=self.wrapped.get_source(environment,template)
        if template=="cfb_box_score.html" and "postgame_pace_and_leverage(" not in source:
            source=source.replace(ANCHOR,REPLACEMENT,1)
        return source,filename,uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped,"list_templates") else []


def _pct(value: Any)->str:
    return "—" if value is None else f"{100*float(value):.1f}%"


def _rate(value: Any)->str:
    return "—" if value is None else f"{float(value):.2f}/min"


def _pace_card(team:str,states:dict[str,Any])->str:
    labels=(("overall","Overall"),("neutral","Neutral"),("leading","Leading"),("trailing","Trailing"),
            ("leading_one_score","Lead ≤8"),("leading_multi_score","Lead 9+"),
            ("trailing_one_score","Trail ≤8"),("trailing_multi_score","Trail 9+"),
            ("standard_downs","Standard downs"),("passing_downs","Passing downs"))
    rows=[]
    for key,label in labels:
        row=states.get(key)
        if not row or not row.get("plays"): continue
        rows.append('<div class="pg-row">'
                    f'<span>{escape(label)} <small>({int(row.get("plays") or 0)})</small></span>'
                    f'<span class="pg-num">{escape(_rate(row.get("play_rate")))}</span>'
                    f'<span class="pg-num">{escape(_pct(row.get("pass_rate")))}</span></div>')
    return ('<article class="pg-analytics-card"><h4>'+escape(team)+'</h4>'
            '<div class="pg-row"><strong>Situation</strong><strong class="pg-num">Play rate</strong><strong class="pg-num">Pass rate</strong></div>'
            +''.join(rows)+'</article>')


def _render(repository,game:dict[str,Any])->Markup:
    game_id=int(game.get("game_id") or 0)
    try: pace=game_pace_summary(repository,game_id)
    except Exception: pace={"teams":{}}
    try: turns=game_turning_points(repository,game_id,model_version=WP_MODEL_VERSION)
    except Exception: turns=[]
    if not pace.get("teams") and not turns: return Markup("")
    pace_cards=''.join(_pace_card(team,states) for team,states in pace.get("teams",{}).items())
    turn_html=[]
    for row in turns:
        period=int(row.get("period") or 0); minute=int(row.get("clock_minutes") or 0); second=int(row.get("clock_seconds") or 0)
        leverage=float(row.get("leverage") or 0)
        wp=row.get("home_win_probability")
        label=f"Q{period} {minute}:{second:02d} · leverage {100*leverage:.1f} pts"
        if wp is not None: label+=f" · home WP {100*float(wp):.1f}%"
        if row.get("terminal_outcome") is not None: label+=" · final play"
        turn_html.append('<div class="pg-turn">'
                         f'<strong>{escape(label)}</strong>'
                         f'<p>{escape(str(row.get("play_text") or row.get("play_type") or "Play"))}</p></div>')
    turning=''.join(turn_html) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'
    return Markup(STYLE+'<section class="section pg-analytics">'
        '<div class="postgame-section-head"><h3>Pace & game state</h3><span>pace-v1 · garbage time excluded</span></div>'
        f'<div class="pg-analytics-grid">{pace_cards}</div>'
        '<p class="pg-note">Neutral = score margin within 8 points. Play rate uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a tempo proxy, not wall-clock seconds to snap. Counts are shown beside each split.</p>'
        f'<div class="postgame-section-head"><h3>Turning points</h3><span>{escape(WP_MODEL_VERSION)} leverage</span></div>'
        f'{turning}</section>')


def install_postgame_analytics_display(app)->None:
    if app.extensions.get("postgame_analytics_display_installed"): return
    repository=app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_pace_and_leverage"]=lambda game:_render(repository,dict(game))
    app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear()
    app.extensions["postgame_analytics_display_installed"]=True
