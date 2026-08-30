"""Supplement the postgame report with compact pace/game-state and WP turning points."""
from __future__ import annotations

from html import escape
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.wp_turning_points import game_turning_points

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_pace_and_leverage(game) }}"
WP_MODEL_VERSION = "wp-v2"

STYLE = '''<style>
.pg-analytics{margin:0 0 24px}.pg-section-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-end;margin:18px 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}.pg-section-head h3{margin:0;font-size:.8rem}.pg-section-head span{font-size:.51rem;color:var(--muted)}
.pg-summary-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}.pg-summary-card{border:1px solid var(--line);background:var(--paper);padding:10px 12px}.pg-summary-card h4{margin:0 0 8px;font-size:.68rem}.pg-summary-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.pg-summary-metric span{display:block;font-size:.48rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:800}.pg-summary-metric strong{display:block;margin-top:3px;font-family:var(--display-font);font-size:.82rem;font-variant-numeric:tabular-nums}
.pg-details{border:1px solid var(--line);background:var(--paper);margin:8px 0 5px}.pg-details summary{cursor:pointer;padding:8px 11px;font-size:.56rem;font-weight:800;text-transform:uppercase;letter-spacing:.055em;color:var(--muted)}.pg-details[open] summary{border-bottom:1px solid var(--line)}.pg-detail-grid{display:grid;grid-template-columns:1fr 1fr}.pg-detail-team{padding:9px 11px}.pg-detail-team+.pg-detail-team{border-left:1px solid var(--line)}.pg-detail-team h4{margin:0 0 6px;font-size:.64rem}.pg-row{display:grid;grid-template-columns:1.35fr .72fr .72fr;gap:8px;padding:4px 0;border-top:1px solid var(--line);font-size:.57rem}.pg-row:first-of-type{border-top:0}.pg-num{text-align:right;font-variant-numeric:tabular-nums}.pg-note{color:var(--muted);font-size:.55rem;line-height:1.45;margin:7px 0 0}
.pg-turning{border:1px solid var(--line);background:var(--paper);margin:8px 0}.pg-turn{display:grid;grid-template-columns:minmax(205px,.72fr) 1.8fr;gap:12px;padding:8px 11px;border-top:1px solid var(--line);align-items:start}.pg-turn:first-child{border-top:0}.pg-turn strong{font-size:.61rem;line-height:1.35}.pg-turn p{margin:0;font-size:.59rem;line-height:1.4}.pg-turn-meta{color:var(--muted);font-size:.48rem;text-transform:uppercase;letter-spacing:.045em;margin-top:2px}
@media(max-width:720px){.pg-summary-grid,.pg-detail-grid{grid-template-columns:1fr}.pg-detail-team+.pg-detail-team{border-left:0;border-top:1px solid var(--line)}.pg-turn{grid-template-columns:1fr}.pg-section-head span{display:none}}@media(max-width:460px){.pg-summary-metrics{grid-template-columns:1fr 1fr}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_pace_and_leverage(" not in source:
            source = source.replace(ANCHOR, REPLACEMENT, 1)
        return source, filename, uptodate
    def list_templates(self):
        return self.wrapped.list_templates() if hasattr(self.wrapped, "list_templates") else []


def _pct(value: Any) -> str:
    return "—" if value is None else f"{100 * float(value):.1f}%"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}/min"


def _summary_card(team: str, states: dict[str, Any]) -> str:
    overall = states.get("overall") or {}
    neutral = states.get("neutral") or {}
    passing = states.get("passing_downs") or {}
    return (
        '<article class="pg-summary-card">'
        f'<h4>{escape(team)}</h4><div class="pg-summary-metrics">'
        '<div class="pg-summary-metric"><span>Overall tempo</span>'
        f'<strong>{escape(_rate(overall.get("play_rate")))}</strong></div>'
        '<div class="pg-summary-metric"><span>Neutral pass rate</span>'
        f'<strong>{escape(_pct(neutral.get("pass_rate")))}</strong></div>'
        '<div class="pg-summary-metric"><span>Passing-down pass</span>'
        f'<strong>{escape(_pct(passing.get("pass_rate")))}</strong></div>'
        '</div></article>'
    )


def _detail_team(team: str, states: dict[str, Any]) -> str:
    labels = (
        ("overall", "Overall"), ("neutral", "Neutral"), ("leading", "Leading"), ("trailing", "Trailing"),
        ("leading_one_score", "Lead ≤8"), ("leading_multi_score", "Lead 9+"),
        ("trailing_one_score", "Trail ≤8"), ("trailing_multi_score", "Trail 9+"),
        ("standard_downs", "Standard downs"), ("passing_downs", "Passing downs"),
    )
    rows = []
    for key, label in labels:
        row = states.get(key)
        if not row or not row.get("plays"): continue
        rows.append(
            '<div class="pg-row">'
            f'<span>{escape(label)} <small>({int(row.get("plays") or 0)})</small></span>'
            f'<span class="pg-num">{escape(_rate(row.get("play_rate")))}</span>'
            f'<span class="pg-num">{escape(_pct(row.get("pass_rate")))}</span></div>'
        )
    return (
        '<section class="pg-detail-team">'
        f'<h4>{escape(team)}</h4>'
        '<div class="pg-row"><strong>Situation</strong><strong class="pg-num">Tempo</strong><strong class="pg-num">Pass</strong></div>'
        + ''.join(rows) + '</section>'
    )


def _render(repository, game: dict[str, Any]) -> Markup:
    game_id = int(game.get("game_id") or 0)
    try: pace = game_pace_summary(repository, game_id)
    except Exception: pace = {"teams": {}}
    try: turns = game_turning_points(repository, game_id, model_version=WP_MODEL_VERSION)
    except Exception: turns = []
    teams = pace.get("teams") or {}
    if not teams and not turns: return Markup("")

    pace_html = ""
    if teams:
        summaries = ''.join(_summary_card(team, states) for team, states in teams.items())
        details = ''.join(_detail_team(team, states) for team, states in teams.items())
        pace_html = (
            '<div class="pg-section-head"><h3>Pace & game state</h3><span>pace-v1 · situational detail on demand</span></div>'
            f'<div class="pg-summary-grid">{summaries}</div>'
            '<details class="pg-details"><summary>View full pace splits</summary>'
            f'<div class="pg-detail-grid">{details}</div></details>'
            '<p class="pg-note">Tempo uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a comparison proxy, not wall-clock seconds to snap.</p>'
        )

    turn_rows = []
    for row in turns:
        period = int(row.get("period") or 0); minute = int(row.get("clock_minutes") or 0); second = int(row.get("clock_seconds") or 0)
        leverage = float(row.get("leverage") or 0); wp = row.get("home_win_probability")
        state = f"Q{period} {minute}:{second:02d} · {100 * leverage:.1f} WP pts"
        if wp is not None: state += f" · home {100 * float(wp):.1f}%"
        terminal = '<div class="pg-turn-meta">Final play</div>' if row.get("terminal_outcome") is not None else ''
        turn_rows.append(
            '<div class="pg-turn">'
            f'<div><strong>{escape(state)}</strong>{terminal}</div>'
            f'<p>{escape(str(row.get("play_text") or row.get("play_type") or "Play"))}</p></div>'
        )
    turning_html = ''.join(turn_rows) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'

    return Markup(
        STYLE + '<section class="section pg-analytics">' + pace_html +
        f'<div class="pg-section-head"><h3>Turning points</h3><span>{escape(WP_MODEL_VERSION)} leverage</span></div>'
        f'<div class="pg-turning">{turning_html}</div></section>'
    )


def install_postgame_analytics_display(app) -> None:
    if app.extensions.get("postgame_analytics_display_installed"): return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_pace_and_leverage"] = lambda game: _render(repository, dict(game))
    app.jinja_loader = _Loader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["postgame_analytics_display_installed"] = True
