"""Render the evidence-based postgame report above the raw box score."""
from __future__ import annotations

from contextlib import closing
from html import escape
from typing import Any
from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.postgame import postgame_report
from sports_aggregator.cfb.pregame_snapshots import final_snapshot
from sports_aggregator.cfb.team_game_advanced import game_summary

REPORT_ANCHOR = '<div id="analysis" class="report-chapter"></div>'
REPORT_INSERT = REPORT_ANCHOR + '\n{{ postgame_analysis(game, team_stats, player_stats) }}'

STYLE = '''<style>
.postgame-shell{margin:0 0 34px}.postgame-report-head{display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.postgame-report-num{font-family:var(--display-font);font-size:.72rem;color:var(--team-light)}.postgame-report-head h2{margin:0;font-size:1rem}.postgame-report-head>span:last-child{font-size:.58rem;color:var(--muted)}
.postgame-story{position:relative;border:1px solid var(--line);background:var(--paper);padding:18px 20px;margin-bottom:18px}.postgame-story:before{content:"";position:absolute;left:-1px;top:-1px;bottom:-1px;width:3px;background:var(--team-light)}
.postgame-lede{font-family:var(--display-font);font-size:clamp(1.04rem,2vw,1.35rem);line-height:1.35;margin:0}.postgame-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:13px}.postgame-tag{border:1px solid var(--line);padding:5px 8px;font-size:.53rem;text-transform:uppercase;letter-spacing:.075em;font-weight:850}
.postgame-section-head{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;margin:22px 0 7px;padding-bottom:7px;border-bottom:1px solid var(--line)}.postgame-section-head h3{margin:0;font-size:.82rem}.postgame-section-head span{font-size:.53rem;color:var(--muted)}
.postgame-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:9px;margin:9px 0 20px}.postgame-factor,.postgame-player,.postgame-role,.postgame-metric,.postgame-expect{border:1px solid var(--line);background:var(--paper);padding:13px 14px}
.postgame-factor strong,.postgame-player strong,.postgame-role strong,.postgame-metric strong,.postgame-expect strong{display:block;font-size:.76rem;margin-bottom:5px}.postgame-factor p,.postgame-player p,.postgame-role p,.postgame-metric p,.postgame-expect p{margin:0;font-size:.66rem;line-height:1.48}.postgame-sub{font-size:.51rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:800}
.postgame-factor .factor-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}.postgame-factor .rank{display:grid;place-items:center;width:25px;height:25px;border:1px solid var(--line);font-family:var(--display-font)}
.postgame-duel{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:end;margin-top:10px}.postgame-duel b{display:inline-block;font-family:var(--display-font);font-size:1.15rem;margin-top:3px}.postgame-duel .vs{font-size:.48rem;color:var(--muted);text-transform:uppercase;padding-bottom:4px}
.postgame-metric.primary{background:color-mix(in srgb,var(--paper) 94%,var(--team-light) 6%);border-top:2px solid var(--team-light)}.postgame-metric.primary .postgame-duel b{font-size:1.32rem}.postgame-coverage,.postgame-model-note{color:var(--muted);font-size:.6rem;line-height:1.5}.postgame-coverage{border-top:1px solid var(--line);padding-top:11px}
@media(max-width:680px){.postgame-grid{grid-template-columns:1fr 1fr}.postgame-report-head>span:last-child,.postgame-section-head span{display:none}}@media(max-width:480px){.postgame-grid{grid-template-columns:1fr}}
</style>'''

class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped=wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == 'cfb_box_score.html' and 'postgame_analysis(' not in source and REPORT_ANCHOR in source:
            source = source.replace(REPORT_ANCHOR, REPORT_INSERT, 1)
        return source, filename, uptodate
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
    with closing(repository._connect()) as c:
        rows=c.execute(f'SELECT player_id,first_name,last_name FROM players WHERE season=? AND player_id IN ({marks})',(season,*ids)).fetchall()
    names={str(r['player_id']):f"{r['first_name']} {r['last_name']}".strip() for r in rows}
    for role in roles: role['player_name']=names.get(str(role.get('player_id') or '')) or 'Current roster player'

def _advanced_html(repository, game):
    try: rows=game_summary(repository,int(game['game_id']),model_version='ep-v1')
    except Exception: rows=[]
    by={str(r.get('team')):r for r in rows}; away_name=str(game.get('away_team') or 'Away'); home_name=str(game.get('home_team') or 'Home'); away=by.get(away_name,{}); home=by.get(home_name,{})
    if not away and not home:return '<div class="empty">Precomputed EPA metrics are not available for this game yet.</div>'
    specs=(('EPA / play','epa_per_play',_f2,1),('Competitive EPA / play','competitive_epa_per_play',_f2,1),('Pass EPA / play','pass_epa_per_play',_f2,0),('Rush EPA / play','rush_epa_per_play',_f2,0),('Early-down EPA / play','early_down_epa_per_play',_f2,0),('Defensive EPA allowed / play','defensive_epa_allowed_per_play',_f2,1),('Success rate','success_rate',_pct,0),('Explosive rate','explosive_rate',_pct,0),('Havoc allowed','havoc_allowed_rate',_pct,0),('Scoring-opportunity rate','scoring_opportunity_rate',_pct,0))
    cards=[]
    for label,key,fmt,primary in specs:
        if away.get(key) is None and home.get(key) is None:continue
        cards.append(f'<article class="postgame-metric{" primary" if primary else ""}"><div class="postgame-sub">{escape(label)}</div><div class="postgame-duel"><span>{escape(away_name)}<br><b>{fmt(away.get(key))}</b></span><span class="vs">vs</span><span style="text-align:right">{escape(home_name)}<br><b>{fmt(home.get(key))}</b></span></div></article>')
    return ''.join(cards)+'<p class="postgame-model-note">EPA is our possession-aware ep-v1 model on qualifying rush/pass snaps. Lower defensive EPA allowed is better.</p>'

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
    return ''.join(cards) or '<div class="empty">The snapshot contains no comparable model or market fields.</div>'

def _render(repository,game,team_stats,player_stats):
    report=postgame_report(repository,game,team_stats or (),player_stats or ()); season=int(game.get('season') or 0); _role_names(repository,season,report['roles'])
    factors=''.join(f'<article class="postgame-factor"><div class="factor-head"><span class="rank">{i}</span><span class="postgame-sub">{escape(str(f.get("confidence") or ""))} confidence</span></div><strong>{escape(str(f.get("headline") or f.get("label") or "Factor"))}</strong><p>{escape(str(f.get("detail") or ""))}</p></article>' for i,f in enumerate(report['factors'],1)) or '<div class="empty">No distinct decisive factor is available.</div>'
    players=[]
    for r in report['players']:
        name=escape(str(r.get('player') or 'Player')); href=url_for('cfb.player_preview',player_id=r.get('player_id'),season=season) if r.get('player_id') else None; shown=f'<a href="{href}">{name}</a>' if href else name
        players.append(f'<article class="postgame-player"><div class="postgame-sub">{escape(str(r.get("team") or ""))}</div><strong>{shown}</strong><p>{escape(str(r.get("summary") or ""))}</p></article>')
    roles=[]
    for r in report['roles']:
        name=escape(str(r.get('player_name') or 'Current roster player')); href=url_for('cfb.player_preview',player_id=r.get('player_id'),season=season) if r.get('player_id') else None; shown=f'<a href="{href}">{name}</a>' if href else name
        roles.append(f'<article class="postgame-role"><div class="postgame-sub">{escape(str(r.get("team") or ""))} · {escape(str(r.get("position") or ""))}</div><strong>{shown}</strong><p>Observed #{int(r.get("observed_rank") or 0)} in recent usage · {int(r.get("games") or 0)} games · {escape(str(r.get("confidence") or "early"))} confidence.</p></article>')
    advanced=_advanced_html(repository,game); expectations=_expectation_html(repository,game); coverage=escape(str((report.get('coverage') or {}).get('coverage_note') or ''))
    return Markup(STYLE+f'<section class="section postgame-shell"><div class="postgame-report-head"><span class="postgame-report-num">01</span><h2>Game analysis</h2><span>Evidence-led postgame intelligence</span></div><div class="postgame-story"><p class="postgame-lede">{escape(str(report["story"]))}</p><div class="postgame-meta"><span class="postgame-tag">{escape(str(report["complexion"]))}</span><span class="postgame-tag">Margin {float(report["margin"]):g}</span><span class="postgame-tag">{len(report["factors"])} measurable separators</span></div></div><div class="postgame-section-head"><h3>What decided it</h3><span>Ranked from stored evidence</span></div><div class="postgame-grid">{factors}</div><div class="postgame-section-head"><h3>Efficiency profile</h3><span>Precomputed ep-v1 · rush/pass snaps</span></div><div class="postgame-grid">{advanced}</div><div class="postgame-section-head"><h3>Expectation vs reality</h3><span>Frozen before kickoff</span></div><div class="postgame-grid">{expectations}</div><div class="postgame-section-head"><h3>Player impact</h3><span>Box-score production</span></div><div class="postgame-grid">{"".join(players) or "<div class=empty>No player impact rows available.</div>"}</div><div class="postgame-section-head"><h3>What may have changed</h3><span>Observed role signal</span></div><div class="postgame-grid">{"".join(roles) or "<div class=empty>No role change signal available.</div>"}</div><div class="postgame-coverage"><strong>Analysis coverage.</strong> {coverage} EPA is our in-house ep-v1 model; CFBD PPA remains only an external benchmark.</div></section>')

def install_postgame_display(app):
    if app.extensions.get('postgame_display_installed'):return
    repository=app.extensions['cfb_repository']; app.jinja_env.globals['postgame_analysis']=lambda game,team_stats,player_stats:_render(repository,dict(game),list(team_stats or ()),list(player_stats or ())); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions['postgame_display_installed']=True
