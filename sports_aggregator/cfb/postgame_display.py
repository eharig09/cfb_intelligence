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

REPORT_ANCHOR = '<div id="analysis" class="report-chapter"></div>'
REPORT_INSERT = REPORT_ANCHOR + '\n{{ postgame_analysis(game, team_stats, player_stats) }}'

STYLE = '''<style>
.postgame-shell{margin:0 0 28px}.postgame-report-head{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;margin-bottom:11px;padding-bottom:7px;border-bottom:1px solid var(--line)}
.postgame-report-num{font-family:var(--display-font);font-size:.68rem;color:var(--team-light)}.postgame-report-head h2{margin:0;font-size:.96rem}.postgame-report-head>span:last-child{font-size:.55rem;color:var(--muted)}
.postgame-story{position:relative;border:1px solid var(--line);background:var(--paper);padding:14px 17px;margin-bottom:14px}.postgame-story:before{content:"";position:absolute;left:-1px;top:-1px;bottom:-1px;width:3px;background:var(--team-light)}
.postgame-lede{font-family:var(--display-font);font-size:clamp(.98rem,1.7vw,1.2rem);line-height:1.38;margin:0;max-width:940px}.postgame-meta{display:flex;gap:5px;flex-wrap:wrap;margin-top:10px}.postgame-tag{border:1px solid var(--line);padding:4px 7px;font-size:.49rem;text-transform:uppercase;letter-spacing:.07em;font-weight:850}
.postgame-section-head{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;margin:17px 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}.postgame-section-head h3{margin:0;font-size:.8rem}.postgame-section-head span{font-size:.51rem;color:var(--muted)}
.postgame-sub{font-size:.49rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:800}.postgame-coverage,.postgame-model-note{color:var(--muted);font-size:.58rem;line-height:1.5}.postgame-coverage{border-top:1px solid var(--line);padding-top:10px;margin-top:13px}
.postgame-evidence-list{border-top:1px solid var(--line);margin:8px 0 14px}.postgame-evidence-row{display:grid;grid-template-columns:34px minmax(190px,.8fr) minmax(0,1.8fr);gap:12px;align-items:start;padding:10px 3px;border-bottom:1px solid var(--line);font-size:.62rem;line-height:1.48}.postgame-evidence-row .rank{font-family:var(--display-font);font-size:.72rem;color:var(--muted);font-variant-numeric:tabular-nums}.postgame-evidence-row strong{display:block;font-size:.67rem}.postgame-evidence-row .factor-confidence{display:block;margin-top:3px;color:var(--muted);font-size:.47rem;text-transform:uppercase;letter-spacing:.055em;font-weight:800}
.efficiency-table{border:1px solid var(--line);background:var(--paper);margin:8px 0 5px}.efficiency-row{display:grid;grid-template-columns:minmax(180px,1.5fr) minmax(110px,.65fr) minmax(110px,.65fr);align-items:center;border-top:1px solid var(--line)}.efficiency-row:first-child{border-top:0}.efficiency-cell{padding:8px 11px;font-size:.63rem}.efficiency-head .efficiency-cell{font-size:.5rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:850;background:color-mix(in srgb,var(--paper) 95%,var(--ink) 5%)}.efficiency-cell.num{text-align:right;font-family:var(--display-font);font-size:.77rem;font-variant-numeric:tabular-nums;display:flex;justify-content:flex-end;align-items:center;gap:7px}.efficiency-cell.edge{font-weight:900}.efficiency-best{font:900 .42rem/1 var(--body-font);letter-spacing:.06em;text-transform:uppercase;border:1px solid var(--team-light);padding:3px 4px;color:var(--ink)}.efficiency-label strong{display:block;font-size:.63rem}.efficiency-label small{display:block;color:var(--muted);font-size:.5rem;margin-top:2px}
.postgame-expect-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin:8px 0 12px}.postgame-expect{border:1px solid var(--line);background:var(--paper);padding:10px 12px}.postgame-expect strong{display:block;font-size:.7rem;margin-top:4px}
.player-impact-columns{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:8px 0 14px}.player-impact-team{border:1px solid var(--line);background:var(--paper)}.player-impact-team h4{margin:0;padding:8px 11px;border-bottom:1px solid var(--line);font-size:.68rem}.player-impact-row{display:grid;grid-template-columns:minmax(130px,.8fr) 1.6fr auto;gap:10px;padding:7px 11px;border-top:1px solid var(--line);font-size:.6rem;line-height:1.4;align-items:center}.player-impact-row:first-of-type{border-top:0}.player-impact-row strong{font-size:.63rem}.player-impact-row p{margin:0}.player-impact-row a{font-weight:800}.player-impact-epa{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.player-impact-epa strong{display:block;font-family:var(--display-font);font-size:.72rem}.player-impact-epa span{display:block;color:var(--muted);font-size:.43rem;text-transform:uppercase;letter-spacing:.045em}
.role-signal{border:1px solid var(--line);background:var(--paper);padding:9px 11px;margin:8px 0 12px;font-size:.61rem;line-height:1.45}.role-signal-row{display:grid;grid-template-columns:minmax(160px,.7fr) 1.8fr;gap:10px;padding:5px 0;border-top:1px solid var(--line)}.role-signal-row:first-child{border-top:0}.role-signal-row strong{font-size:.63rem}
@media(max-width:760px){.postgame-evidence-row{grid-template-columns:28px 1fr}.postgame-evidence-row>span:last-child{grid-column:2}.player-impact-columns{grid-template-columns:1fr}.postgame-report-head>span:last-child,.postgame-section-head span{display:none}}
@media(max-width:520px){.efficiency-row{grid-template-columns:1.25fr .8fr .8fr}.efficiency-cell{padding:7px 7px}.efficiency-best{font-size:.38rem}.player-impact-row{grid-template-columns:1fr auto}.player-impact-row p{grid-column:1}.player-impact-epa{grid-column:2;grid-row:1 / span 2}.role-signal-row{grid-template-columns:1fr}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == 'cfb_box_score.html' and 'postgame_analysis(' not in source and REPORT_ANCHOR in source:
            source = source.replace(REPORT_ANCHOR, REPORT_INSERT, 1)
        return source, filename, uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped, 'list_templates') else []


def _pct(v):
    try: return f'{100 * float(v):.1f}%'
    except (TypeError, ValueError): return '—'


def _f2(v):
    try: return f'{float(v):+.2f}'
    except (TypeError, ValueError): return '—'


def _f1(v):
    try: return f'{float(v):.1f}'
    except (TypeError, ValueError): return '—'


def _role_names(repository, season: int, roles: list[dict[str, Any]]) -> None:
    ids = sorted({str(r['player_id']) for r in roles if r.get('player_id')})
    if not ids: return
    marks = ','.join('?' for _ in ids)
    with closing(repository._connect()) as c:
        rows = c.execute(
            f'SELECT player_id,first_name,last_name FROM players WHERE season=? AND player_id IN ({marks})',
            (season, *ids),
        ).fetchall()
    names = {str(r['player_id']): f"{r['first_name']} {r['last_name']}".strip() for r in rows}
    for role in roles:
        role['player_name'] = names.get(str(role.get('player_id') or '')) or 'Current roster player'


def _advanced_html(repository, game):
    try:
        rows = game_summary(repository, int(game['game_id']), model_version='ep-v1')
    except Exception:
        rows = []
    by = {str(r.get('team')): r for r in rows}
    away_name = str(game.get('away_team') or 'Away')
    home_name = str(game.get('home_team') or 'Home')
    away = by.get(away_name, {})
    home = by.get(home_name, {})
    if not away and not home:
        return '<div class="empty">Precomputed EPA metrics are not available for this game yet.</div>'
    specs = (
        ('EPA / play', 'epa_per_play', _f2, False, 'All qualifying rush/pass snaps'),
        ('Competitive EPA / play', 'competitive_epa_per_play', _f2, False, 'Current non-garbage split'),
        ('Pass EPA / play', 'pass_epa_per_play', _f2, False, None),
        ('Rush EPA / play', 'rush_epa_per_play', _f2, False, None),
        ('Early-down EPA / play', 'early_down_epa_per_play', _f2, False, '1st and 2nd down'),
        ('Defensive EPA allowed / play', 'defensive_epa_allowed_per_play', _f2, True, 'Lower is better'),
        ('Success rate', 'success_rate', _pct, False, None),
        ('Explosive-play rate', 'explosive_rate', _pct, False, None),
        ('Havoc allowed', 'havoc_allowed_rate', _pct, True, 'Lower is better'),
        ('Scoring-opportunity rate', 'scoring_opportunity_rate', _pct, False, None),
    )
    body = []
    for label, key, fmt, lower_better, note in specs:
        av, hv = away.get(key), home.get(key)
        if av is None and hv is None:
            continue
        aedge = hedge = False
        try:
            if av is not None and hv is not None and float(av) != float(hv):
                away_better = float(av) < float(hv) if lower_better else float(av) > float(hv)
                aedge = away_better
                hedge = not away_better
        except (TypeError, ValueError):
            pass
        note_html = f'<small>{escape(note)}</small>' if note else ''
        abadge = '<span class="efficiency-best">Best</span>' if aedge else ''
        hbadge = '<span class="efficiency-best">Best</span>' if hedge else ''
        body.append(
            '<div class="efficiency-row">'
            f'<div class="efficiency-cell efficiency-label"><strong>{escape(label)}</strong>{note_html}</div>'
            f'<div class="efficiency-cell num{" edge" if aedge else ""}">{fmt(av)}{abadge}</div>'
            f'<div class="efficiency-cell num{" edge" if hedge else ""}">{fmt(hv)}{hbadge}</div>'
            '</div>'
        )
    return (
        '<div class="efficiency-table">'
        '<div class="efficiency-row efficiency-head">'
        '<div class="efficiency-cell">Metric</div>'
        f'<div class="efficiency-cell" style="text-align:right">{escape(away_name)}</div>'
        f'<div class="efficiency-cell" style="text-align:right">{escape(home_name)}</div>'
        '</div>' + ''.join(body) + '</div>'
        '<p class="postgame-model-note">EPA is our possession-aware ep-v1 model on qualifying rush/pass snaps. The stronger value is highlighted in that team’s color; lower is better where explicitly noted.</p>'
    )


def _expectation_html(repository, game):
    try: snap = final_snapshot(repository, int(game['game_id']))
    except Exception: snap = None
    if not snap:
        return '<div class="empty">No frozen pregame snapshot exists for this game.</div>'
    p = snap.get('payload') or {}; market = p.get('market') or {}; elo = p.get('elo') or {}; fpi = p.get('fpi') or {}; cards = []
    if market.get('consensus_spread') is not None or market.get('consensus_total') is not None:
        cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Market at snapshot</div><strong>Spread {_f1(market.get("consensus_spread"))} · Total {_f1(market.get("consensus_total"))}</strong></article>')
    he = (elo.get('home') or {}).get('elo'); ae = (elo.get('away') or {}).get('elo')
    if he is not None or ae is not None:
        cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Pregame Elo</div><strong>{escape(str(game.get("away_team")))} {_f1(ae)} · {escape(str(game.get("home_team")))} {_f1(he)}</strong></article>')
    hw = fpi.get('homeWinProb') or fpi.get('home_win_prob'); aw = fpi.get('awayWinProb') or fpi.get('away_win_prob')
    if hw is not None or aw is not None:
        cards.append(f'<article class="postgame-expect"><div class="postgame-sub">Pregame FPI</div><strong>Away {_pct(aw)} · Home {_pct(hw)}</strong></article>')
    return '<div class="postgame-expect-grid">' + ''.join(cards) + '</div>' if cards else '<div class="empty">The snapshot contains no comparable model or market fields.</div>'


def _factor_html(report):
    factors = list(report.get('factors') or [])
    if not factors:
        return '<div class="empty">No distinct decisive factor is available.</div>'
    rows = []
    for i, f in enumerate(factors, 1):
        confidence = str(f.get('confidence') or '').strip()
        confidence_html = f'<span class="factor-confidence">{escape(confidence)} confidence</span>' if confidence else ''
        rows.append(
            '<div class="postgame-evidence-row">'
            f'<span class="rank">{i:02d}</span>'
            f'<strong>{escape(str(f.get("headline") or f.get("label") or "Factor"))}{confidence_html}</strong>'
            f'<span>{escape(str(f.get("detail") or ""))}</span></div>'
        )
    return '<div class="postgame-evidence-list">' + ''.join(rows) + '</div>'


def _players_html(report, game, season):
    grouped = defaultdict(list)
    for row in report.get('players') or []:
        grouped[str(row.get('team') or 'Other')].append(row)
    teams = [str(game.get('away_team') or 'Away'), str(game.get('home_team') or 'Home')]
    columns = []
    for team in teams:
        rows = []
        for r in grouped.get(team, []):
            name = escape(str(r.get('player') or 'Player'))
            href = url_for('cfb.player_preview', player_id=r.get('player_id'), season=season) if r.get('player_id') else None
            shown = f'<a href="{href}">{name}</a>' if href else name
            epa_html = ''
            if r.get('involved_epa') is not None:
                epa_html = (
                    '<div class="player-impact-epa">'
                    f'<strong>{_f2(r.get("involved_epa"))}</strong>'
                    f'<span>EPA · {int(r.get("epa_plays") or 0)} involved plays</span></div>'
                )
            rows.append(
                '<div class="player-impact-row">'
                f'<strong>{shown}</strong><p>{escape(str(r.get("summary") or "Recorded game impact"))}</p>{epa_html}</div>'
            )
        columns.append(
            '<section class="player-impact-team">'
            f'<h4>{escape(team)}</h4>' + (''.join(rows) or '<div class="player-impact-row"><span>—</span><p>No impact rows stored.</p></div>') + '</section>'
        )
    return (
        '<div class="player-impact-columns">' + ''.join(columns) + '</div>'
        '<p class="postgame-model-note">Player EPA is team-perspective EPA on plays where the player is explicitly identified in the stored description. It is involvement credit, not additive individual EPA; a QB and receiver can share the same play EPA.</p>'
    )


def _roles_html(report, season):
    rows = []
    for r in report.get('roles') or []:
        name = escape(str(r.get('player_name') or 'Current roster player'))
        href = url_for('cfb.player_preview', player_id=r.get('player_id'), season=season) if r.get('player_id') else None
        shown = f'<a href="{href}">{name}</a>' if href else name
        rows.append(
            '<div class="role-signal-row">'
            f'<strong>{shown}</strong>'
            f'<span>{escape(str(r.get("team") or ""))} · {escape(str(r.get("position") or ""))} · observed #{int(r.get("observed_rank") or 0)} · {int(r.get("games") or 0)} games · {escape(str(r.get("confidence") or "early"))} confidence</span></div>'
        )
    return '<div class="role-signal">' + ''.join(rows) + '</div>' if rows else '<div class="empty">No multi-game role change has cleared the observed-depth threshold yet.</div>'


def _render(repository, game, team_stats, player_stats):
    report = postgame_report(repository, game, team_stats or (), player_stats or ())
    season = int(game.get('season') or 0)
    _role_names(repository, season, report['roles'])
    try:
        annotate_player_epa(repository, game, report['players'], model_version='ep-v1')
    except Exception:
        pass
    advanced = _advanced_html(repository, game)
    expectations = _expectation_html(repository, game)
    factors = _factor_html(report)
    players = _players_html(report, game, season)
    roles = _roles_html(report, season)
    coverage = escape(str((report.get('coverage') or {}).get('coverage_note') or ''))
    return Markup(
        STYLE + '<section class="section postgame-shell">'
        '<div class="postgame-report-head"><span class="postgame-report-num">01</span><h2>Game analysis</h2><span>Evidence-led postgame intelligence</span></div>'
        '<div class="postgame-story">'
        f'<p class="postgame-lede">{escape(str(report["story"]))}</p><div class="postgame-meta">'
        f'<span class="postgame-tag">{escape(str(report["complexion"]))}</span>'
        f'<span class="postgame-tag">Margin {float(report["margin"]):g}</span>'
        f'<span class="postgame-tag">{len(report["factors"])} measurable separators</span></div></div>'
        '<div class="postgame-section-head"><h3>What decided it</h3><span>Ranked measurable evidence</span></div>'
        f'{factors}'
        '<div class="postgame-section-head"><h3>Efficiency profile</h3><span>Precomputed ep-v1 · rush/pass snaps</span></div>'
        f'{advanced}'
        '<div class="postgame-section-head"><h3>Expectation vs reality</h3><span>Frozen before kickoff</span></div>'
        f'{expectations}'
        '<div class="postgame-section-head"><h3>Player impact</h3><span>Production + involved-play ep-v1</span></div>'
        f'{players}'
        '<div class="postgame-section-head"><h3>What may have changed</h3><span>Observed role signal</span></div>'
        f'{roles}'
        '<div class="postgame-coverage"><strong>Analysis coverage.</strong> '
        f'{coverage} EPA is our in-house ep-v1 model; CFBD PPA remains only an external benchmark.</div></section>'
    )


def install_postgame_display(app):
    if app.extensions.get('postgame_display_installed'): return
    repository = app.extensions['cfb_repository']
    app.jinja_env.globals['postgame_analysis'] = lambda game, team_stats, player_stats: _render(
        repository, dict(game), list(team_stats or ()), list(player_stats or ())
    )
    app.jinja_loader = _Loader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions['postgame_display_installed'] = True
