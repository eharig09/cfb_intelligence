"""Render the evidence-based postgame report above the raw box score."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.player_game_epa import annotate_player_epa
from sports_aggregator.cfb.postgame import postgame_report
from sports_aggregator.cfb.pregame_snapshots import final_snapshot
from sports_aggregator.cfb.team_game_advanced import game_summary

EPA_MODEL_VERSION = "ep-v2"
REPORT_ANCHOR = '<div id="analysis" class="report-chapter"></div>'
REPORT_INSERT = REPORT_ANCHOR + '\n{{ postgame_analysis(game, team_stats, player_stats) }}'

STYLE = ""  # served from static/cfb_analysis.css, not inlined per render


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped=wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source,filename,uptodate=self.wrapped.get_source(environment,template)
        if template=='cfb_box_score.html' and 'postgame_analysis(' not in source and REPORT_ANCHOR in source:
            source=source.replace(REPORT_ANCHOR,REPORT_INSERT,1)
        return source,filename,uptodate
    def list_templates(self): return self.wrapped.list_templates() if hasattr(self.wrapped,'list_templates') else []


def _pct(v):
    try:return f'{100*float(v):.1f}%'
    except (TypeError,ValueError):return '—'

def _f2(v):
    try:return f'{float(v):+.2f}'
    except (TypeError,ValueError):return '—'

def _f1(v):
    try:return f'{float(v):.1f}'
    except (TypeError,ValueError):return '—'


def _role_names(repository, season:int, roles:list[dict[str,Any]])->None:
    ids=sorted({str(r['player_id']) for r in roles if r.get('player_id')})
    if not ids:return
    marks=','.join('?' for _ in ids)
    with repository._reader() as c:
        rows=c.execute(f'SELECT player_id,first_name,last_name FROM players WHERE season=? AND player_id IN ({marks})',(season,*ids)).fetchall()
    names={str(r['player_id']):f"{r['first_name']} {r['last_name']}".strip() for r in rows}
    for role in roles: role['player_name']=names.get(str(role.get('player_id') or '')) or 'Current roster player'


def _advanced_html(repository, game):
    try: rows=game_summary(repository,int(game['game_id']),model_version=EPA_MODEL_VERSION)
    except Exception: rows=[]
    by={str(r.get('team')):r for r in rows}; away_name=str(game.get('away_team') or 'Away'); home_name=str(game.get('home_team') or 'Home'); away=by.get(away_name,{}); home=by.get(home_name,{})
    if not away and not home:return '<div class="empty">Precomputed EPA metrics are not available for this game yet.</div>'
    specs=(('EPA / play','epa_per_play',_f2,False,'All qualifying rush/pass snaps'),('Competitive EPA / play','competitive_epa_per_play',_f2,False,'Current non-garbage split'),('Pass EPA / play','pass_epa_per_play',_f2,False,None),('Rush EPA / play','rush_epa_per_play',_f2,False,None),('Early-down EPA / play','early_down_epa_per_play',_f2,False,'1st and 2nd down'),('Defensive EPA allowed / play','defensive_epa_allowed_per_play',_f2,True,'Lower is better'),('Success rate','success_rate',_pct,False,None),('Explosive-play rate','explosive_rate',_pct,False,None),('Havoc allowed','havoc_allowed_rate',_pct,True,'Lower is better'),('Scoring-opportunity rate','scoring_opportunity_rate',_pct,False,None))
    body=[]
    for label,key,fmt,lower_better,note in specs:
        av,hv=away.get(key),home.get(key)
        if av is None and hv is None:continue
        aedge=hedge=False
        try:
            if av is not None and hv is not None and float(av)!=float(hv):
                aedge=float(av)<float(hv) if lower_better else float(av)>float(hv); hedge=not aedge
        except (TypeError,ValueError):pass
        note_html=f'<small>{escape(note)}</small>' if note else ''
        body.append('<div class="efficiency-row">'+f'<div class="efficiency-cell efficiency-label"><strong>{escape(label)}</strong>{note_html}</div>'+f'<div class="efficiency-cell num{" edge" if aedge else ""}">{fmt(av)}</div>'+f'<div class="efficiency-cell num{" edge" if hedge else ""}">{fmt(hv)}</div></div>')
    return '<div class="efficiency-table"><div class="efficiency-row efficiency-head"><div class="efficiency-cell">Metric</div>'+f'<div class="efficiency-cell" style="text-align:right">{escape(away_name)}</div><div class="efficiency-cell" style="text-align:right">{escape(home_name)}</div></div>'+''.join(body)+'</div>'+f'<p class="postgame-model-note">EPA is our event-aligned {EPA_MODEL_VERSION} model on qualifying rush/pass snaps. The stronger value is highlighted in that team’s color; lower is better where explicitly noted.</p>'


def _expectation_html(repository,game):
    try:snap=final_snapshot(repository,int(game['game_id']))
    except Exception:snap=None
    if not snap:return '<div class="empty">No frozen pregame snapshot exists for this game.</div>'
    p=snap.get('payload') or {}; market=p.get('market') or {}; elo=p.get('elo') or {}; fpi=p.get('fpi') or {}; cards=[]
    if market.get('consensus_spread') is not None or market.get('consensus_total') is not None:cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Market at snapshot</div><strong>Spread {_f1(market.get("consensus_spread"))} · Total {_f1(market.get("consensus_total"))}</strong></article>')
    he=(elo.get('home') or {}).get('elo'); ae=(elo.get('away') or {}).get('elo')
    if he is not None or ae is not None:cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Pregame Elo</div><strong>{escape(str(game.get("away_team")))} {_f1(ae)} · {escape(str(game.get("home_team")))} {_f1(he)}</strong></article>')
    hw=fpi.get('homeWinProb') or fpi.get('home_win_prob'); aw=fpi.get('awayWinProb') or fpi.get('away_win_prob')
    if hw is not None or aw is not None:cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Pregame FPI</div><strong>Away {_pct(aw)} · Home {_pct(hw)}</strong></article>')
    return '<div class="postgame-expect-grid">'+''.join(cards)+'</div>' if cards else '<div class="empty">The snapshot contains no comparable model or market fields.</div>'


def _factor_html(report):
    factors=list(report.get('factors') or [])
    if not factors:return '<div class="empty">No distinct decisive factor is available.</div>'
    rows=[]
    for i,f in enumerate(factors,1):
        confidence=str(f.get('confidence') or '').strip(); confidence_html=f'<span class="factor-confidence">{escape(confidence)} confidence</span>' if confidence else ''
        rows.append('<div class="postgame-evidence-row">'+f'<span class="rank">{i:02d}</span><strong>{escape(str(f.get("headline") or f.get("label") or "Factor"))}{confidence_html}</strong><span>{escape(str(f.get("detail") or ""))}</span></div>')
    return '<div class="postgame-evidence-list">'+''.join(rows)+'</div>'


def _players_html(report,game,season):
    grouped=defaultdict(list)
    for row in report.get('players') or []:grouped[str(row.get('team') or 'Other')].append(row)
    columns=[]
    for team in [str(game.get('away_team') or 'Away'),str(game.get('home_team') or 'Home')]:
        rows=[]
        for r in grouped.get(team,[]):
            name=escape(str(r.get('player') or 'Player')); href=url_for('cfb.player_preview',player_id=r.get('player_id'),season=season) if r.get('player_id') else None; shown=f'<a href="{href}">{name}</a>' if href else name
            epa_html=''
            if r.get('involved_epa') is not None:epa_html=f'<div class="player-impact-epa"><strong>{_f2(r.get("involved_epa"))}</strong><span>EPA · {int(r.get("epa_plays") or 0)} involved plays</span></div>'
            rows.append(f'<div class="player-impact-row"><strong>{shown}</strong><p>{escape(str(r.get("summary") or "Recorded game impact"))}</p>{epa_html}</div>')
        columns.append(f'<section class="player-impact-team"><h4>{escape(team)}</h4>'+(''.join(rows) or '<div class="player-impact-row"><span>—</span><p>No impact rows stored.</p></div>')+'</section>')
    return '<div class="player-impact-columns">'+''.join(columns)+'</div>'+f'<p class="postgame-model-note">Player EPA uses event-aligned {EPA_MODEL_VERSION} from the team perspective on plays where the player is explicitly identified. It is involvement credit, not additive individual EPA.</p>'


def _roles_html(report,season):
    rows=[]
    for r in report.get('roles') or []:
        name=escape(str(r.get('player_name') or 'Current roster player')); href=url_for('cfb.player_preview',player_id=r.get('player_id'),season=season) if r.get('player_id') else None; shown=f'<a href="{href}">{name}</a>' if href else name
        rows.append(f'<div class="role-signal-row"><strong>{shown}</strong><span>{escape(str(r.get("team") or ""))} · {escape(str(r.get("position") or ""))} · observed #{int(r.get("observed_rank") or 0)} · {int(r.get("games") or 0)} games · {escape(str(r.get("confidence") or "early"))} confidence</span></div>')
    return '<div class="role-signal">'+''.join(rows)+'</div>' if rows else '<div class="empty">No multi-game role change has cleared the observed-depth threshold yet.</div>'


def _render(repository,game,team_stats,player_stats):
    report=postgame_report(repository,game,team_stats or (),player_stats or ()); season=int(game.get('season') or 0); _role_names(repository,season,report['roles'])
    try:annotate_player_epa(repository,game,report['players'],model_version=EPA_MODEL_VERSION)
    except Exception:pass
    advanced=_advanced_html(repository,game); expectations=_expectation_html(repository,game); factors=_factor_html(report); players=_players_html(report,game,season); roles=_roles_html(report,season); coverage=escape(str((report.get('coverage') or {}).get('coverage_note') or ''))
    return Markup(STYLE+'<section class="section postgame-shell">'+'<div class="postgame-report-head"><span class="postgame-report-num">01</span><h2>Game analysis</h2><span>Evidence-led postgame intelligence</span></div>'+'<div class="postgame-story">'+f'<p class="postgame-lede">{escape(str(report["story"]))}</p><div class="postgame-meta"><span class="postgame-tag">{escape(str(report["complexion"]))}</span><span class="postgame-tag">Margin {float(report["margin"]):g}</span><span class="postgame-tag">{len(report["factors"])} measurable separators</span></div></div>'+'<div class="postgame-section-head"><h3>What decided it</h3><span>Ranked measurable evidence</span></div>'+f'{factors}' + f'<div class="postgame-section-head"><h3>Efficiency profile</h3><span>Precomputed {EPA_MODEL_VERSION} · rush/pass snaps</span></div>{advanced}'+'<div class="postgame-section-head"><h3>Expectation vs reality</h3><span>Frozen before kickoff</span></div>'+f'{expectations}'+f'<div class="postgame-section-head"><h3>Player impact</h3><span>Production + involved-play {EPA_MODEL_VERSION}</span></div>{players}'+'<div class="postgame-section-head"><h3>What may have changed</h3><span>Observed role signal</span></div>'+f'{roles}<div class="postgame-coverage"><strong>Analysis coverage.</strong> {coverage} EPA is our in-house event-aligned {EPA_MODEL_VERSION} model; CFBD PPA remains only an external benchmark.</div></section>')


def install_postgame_display(app):
    if app.extensions.get('postgame_display_installed'):return
    repository=app.extensions['cfb_repository']; app.jinja_env.globals['postgame_analysis']=lambda game,team_stats,player_stats:_render(repository,dict(game),list(team_stats or ()),list(player_stats or ())); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions['postgame_display_installed']=True
