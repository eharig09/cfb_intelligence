"""Render the evidence-based postgame report above the raw box score."""

from __future__ import annotations

from contextlib import closing
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.postgame import postgame_report
from sports_aggregator.cfb.play_by_play import game_advanced_summary
from sports_aggregator.cfb.pregame_snapshots import final_snapshot


BOX_ANCHOR = '''
<section class="section">
    <h2>Team statistics</h2>
'''
BOX_INSERT = '''
{{ postgame_analysis(game, team_stats, player_stats) }}

<section class="section">
    <h2>Team statistics</h2>
'''

STYLE = '''
<style>
.postgame-shell{margin:20px 0 28px}.postgame-lede{font-size:1rem;line-height:1.55;max-width:900px}
.postgame-meta{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 16px}.postgame-tag{border:1px solid var(--line);padding:4px 7px;font-size:.58rem;text-transform:uppercase;letter-spacing:.06em;font-weight:800}
.postgame-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin:10px 0 18px}
.postgame-factor,.postgame-player,.postgame-role,.postgame-metric,.postgame-expect{border:1px solid var(--line);background:var(--paper);padding:11px 12px}
.postgame-factor strong,.postgame-player strong,.postgame-role strong,.postgame-metric strong,.postgame-expect strong{display:block;font-size:.78rem;margin-bottom:4px}.postgame-factor p,.postgame-player p,.postgame-role p,.postgame-metric p,.postgame-expect p{margin:0;font-size:.7rem;line-height:1.4}
.postgame-factor .factor-head{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:5px}.postgame-factor .rank{font-family:var(--display-font);font-size:1.15rem}.postgame-sub{color:var(--muted);font-size:.58rem;text-transform:uppercase;letter-spacing:.045em}.postgame-coverage{border-left:3px solid var(--line);padding:8px 11px;color:var(--muted);font-size:.66rem;line-height:1.45;margin-top:12px}
.postgame-section-head{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin-top:20px}.postgame-section-head h3{margin:0}.postgame-section-head span{color:var(--muted);font-size:.58rem}.postgame-duel{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:baseline;margin-top:6px}.postgame-duel b{font-variant-numeric:tabular-nums}.postgame-duel .vs{font-size:.52rem;color:var(--muted);text-transform:uppercase}
</style>
'''


class _PostgameLoader(BaseLoader):
    def __init__(self, wrapped: BaseLoader):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_analysis(" not in source:
            source = source.replace(BOX_ANCHOR, BOX_INSERT, 1)
        return source, filename, uptodate

    def list_templates(self):
        if hasattr(self.wrapped, "list_templates"):
            return self.wrapped.list_templates()
        return []


def _player_url(player_id: Any, season: int) -> str | None:
    if not player_id:
        return None
    return url_for("cfb.player_preview", player_id=player_id, season=season)


def _pct(value: Any) -> str:
    try:
        return f"{100*float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _f1(value: Any) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _role_names(repository, season: int, roles: list[dict[str, Any]]) -> None:
    ids = sorted({str(row["player_id"]) for row in roles if row.get("player_id")})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with closing(repository._connect()) as connection:
        rows = connection.execute(
            f"""SELECT player_id,first_name,last_name,team FROM players
                WHERE season=? AND player_id IN ({placeholders})""",
            (season, *ids),
        ).fetchall()
    names = {
        str(row["player_id"]): " ".join(
            part for part in (row["first_name"], row["last_name"]) if part
        ).strip()
        for row in rows
    }
    for role in roles:
        role["player_name"] = names.get(str(role.get("player_id") or "")) or "Current roster player"


def _advanced_html(repository, game: dict[str, Any]) -> str:
    try:
        packet = game_advanced_summary(repository, int(game["game_id"]))
    except Exception:
        return '<div class="empty">Play-by-play derived metrics are not available for this game yet.</div>'
    teams = packet.get("teams") or {}
    away = teams.get(str(game.get("away_team"))) or {}
    home = teams.get(str(game.get("home_team"))) or {}
    if not away and not home:
        return '<div class="empty">Play-by-play has not been stored for this game yet.</div>'
    specs = (
        ("Success rate", "success_rate", _pct),
        ("Pass success", "pass_success_rate", _pct),
        ("Rush success", "rush_success_rate", _pct),
        ("Explosive-play rate", "explosive_rate", _pct),
        ("Havoc allowed", "havoc_allowed_rate", _pct),
        ("Line yards / rush", "line_yards_per_rush", _f1),
        ("CFBD PPA / play", "provider_ppa_per_play", _f1),
    )
    cards = []
    for label, key, formatter in specs:
        if away.get(key) is None and home.get(key) is None:
            continue
        cards.append(
            '<article class="postgame-metric">'
            f'<div class="postgame-sub">{escape(label)}</div>'
            '<div class="postgame-duel">'
            f'<span>{escape(str(game.get("away_team") or "Away"))}<br><b>{formatter(away.get(key))}</b></span>'
            '<span class="vs">vs</span>'
            f'<span style="text-align:right">{escape(str(game.get("home_team") or "Home"))}<br><b>{formatter(home.get(key))}</b></span>'
            '</div></article>'
        )
    return "".join(cards) or '<div class="empty">No qualifying scrimmage-play metrics are available.</div>'


def _expectation_html(repository, game: dict[str, Any]) -> str:
    try:
        snap = final_snapshot(repository, int(game["game_id"]))
    except Exception:
        snap = None
    if not snap:
        return '<div class="empty">No frozen pregame snapshot exists for this game. Future games will preserve staged expectations before kickoff.</div>'
    payload = snap.get("payload") or {}
    market = payload.get("market") or {}
    fpi = payload.get("fpi") or {}
    elo = payload.get("elo") or {}
    cards = []
    if market.get("consensus_spread") is not None or market.get("consensus_total") is not None:
        cards.append(
            '<article class="postgame-expect"><div class="postgame-sub">Market at snapshot</div>'
            f'<strong>Spread {_f1(market.get("consensus_spread"))} · Total {_f1(market.get("consensus_total"))}</strong>'
            f'<p>{int(market.get("count") or 0)} stored provider quote(s).</p></article>'
        )
    home_elo = (elo.get("home") or {}).get("elo"); away_elo = (elo.get("away") or {}).get("elo")
    if home_elo is not None or away_elo is not None:
        cards.append(
            '<article class="postgame-expect"><div class="postgame-sub">Pregame Elo</div>'
            f'<strong>{escape(str(game.get("away_team")))} {_f1(away_elo)} · {escape(str(game.get("home_team")))} {_f1(home_elo)}</strong>'
            '<p>Frozen before the final result was stored.</p></article>'
        )
    if fpi:
        home_wp = fpi.get("homeWinProb") or fpi.get("home_win_prob")
        away_wp = fpi.get("awayWinProb") or fpi.get("away_win_prob")
        if home_wp is not None or away_wp is not None:
            cards.append(
                '<article class="postgame-expect"><div class="postgame-sub">Pregame FPI</div>'
                f'<strong>Away {_pct(away_wp)} · Home {_pct(home_wp)}</strong>'
                '<p>Provider model captured in the immutable packet.</p></article>'
            )
    watches = payload.get("player_watches") or []
    if watches:
        top = watches[0]
        cards.append(
            '<article class="postgame-expect"><div class="postgame-sub">Top player/unit watch</div>'
            f'<strong>{escape(str(top.get("label") or "Pregame matchup"))}</strong>'
            f'<p>{escape(str(top.get("why") or "This matchup was identified before kickoff."))}</p></article>'
        )
    meta = (
        f'<div class="postgame-coverage"><strong>Frozen expectation:</strong> {escape(str(snap.get("stage") or "pregame"))} '
        f'captured {abs(float(snap.get("hours_to_kick") or 0)):.1f} hours before kickoff. '
        'This packet is immutable, so postgame comparisons cannot accidentally use information learned after the game.</div>'
    )
    return ("".join(cards) or '<div class="empty">The snapshot exists but contains no comparable model/market fields.</div>') + meta


def _render(repository, game: dict[str, Any], team_stats, player_stats) -> Markup:
    report = postgame_report(repository, game, team_stats or (), player_stats or ())
    season = int(game.get("season") or 0)
    _role_names(repository, season, report["roles"])

    factor_cards = []
    for index, factor in enumerate(report["factors"], 1):
        factor_cards.append(
            '<article class="postgame-factor"><div class="factor-head">'
            f'<span class="rank">{index}</span><span class="postgame-sub">{escape(str(factor.get("confidence") or ""))} confidence</span></div>'
            f'<strong>{escape(str(factor.get("headline") or factor.get("label") or "Factor"))}</strong>'
            f'<p>{escape(str(factor.get("detail") or ""))}</p>'
            f'<div class="postgame-sub" style="margin-top:6px">{escape(str(factor.get("source") or ""))}</div></article>'
        )
    factors_html = "".join(factor_cards) or '<div class="empty">The stored box score does not yet expose a statistically distinct decisive factor.</div>'

    player_cards = []
    for row in report["players"]:
        href = _player_url(row.get("player_id"), season)
        name = escape(str(row.get("player") or "Player")); name_html = f'<a href="{href}">{name}</a>' if href else name
        player_cards.append(
            '<article class="postgame-player">'
            f'<div class="postgame-sub">{escape(str(row.get("team") or ""))}</div><strong>{name_html}</strong>'
            f'<p>{escape(str(row.get("summary") or ""))}</p></article>'
        )
    players_html = "".join(player_cards) or '<div class="empty">No player-level impact rows are stored for this game yet.</div>'

    role_cards = []
    for row in report["roles"]:
        href = _player_url(row.get("player_id"), season)
        name = escape(str(row.get("player_name") or "Current roster player")); name_html = f'<a href="{href}">{name}</a>' if href else name
        week = row.get("latest_week"); through = f" through Week {week}" if week is not None else ""
        role_cards.append(
            '<article class="postgame-role">'
            f'<div class="postgame-sub">{escape(str(row.get("team") or ""))} · {escape(str(row.get("position") or ""))}</div>'
            f'<strong>{name_html}</strong><p>Observed #{int(row.get("observed_rank") or 0)} in recent game usage · '
            f'{int(row.get("games") or 0)} games{escape(through)} · {escape(str(row.get("confidence") or "early"))} confidence.</p></article>'
        )
    roles_html = "".join(role_cards) or '<div class="empty">No multi-game role change has cleared the observed-depth threshold yet.</div>'

    coverage = report["coverage"]
    advanced_html = _advanced_html(repository, game)
    expectation_html = _expectation_html(repository, game)
    return Markup(
        STYLE + '<section class="section postgame-shell"><div class="eyebrow">Postgame intelligence</div>'
        '<h2>Game story & analysis</h2>'
        f'<p class="postgame-lede">{escape(str(report["story"]))}</p><div class="postgame-meta">'
        f'<span class="postgame-tag">{escape(str(report["complexion"]))}</span>'
        f'<span class="postgame-tag">Margin {float(report["margin"]):g}</span>'
        f'<span class="postgame-tag">{len(report["factors"])} measurable separators</span></div>'
        '<div class="postgame-section-head"><h3>What decided it</h3><span>Ranked from stored evidence</span></div>'
        f'<div class="postgame-grid">{factors_html}</div>'
        '<div class="postgame-section-head"><h3>How the game was played</h3><span>Our pbp-v1 definitions · garbage time excluded</span></div>'
        f'<div class="postgame-grid">{advanced_html}</div>'
        '<div class="postgame-section-head"><h3>Expectation vs reality</h3><span>Frozen before kickoff</span></div>'
        f'<div class="postgame-grid">{expectation_html}</div>'
        '<div class="postgame-section-head"><h3>Player impact</h3><span>Box-score production, not a subjective grade</span></div>'
        f'<div class="postgame-grid">{players_html}</div>'
        '<div class="postgame-section-head"><h3>What may have changed</h3><span>Current-roster observed role signal</span></div>'
        f'<div class="postgame-grid">{roles_html}</div>'
        '<div class="postgame-coverage"><strong>Analysis coverage.</strong> '
        f'{escape(str(coverage.get("coverage_note") or ""))} '
        'A factor is omitted when the underlying field is absent or the difference is too small to support a meaningful claim. '
        'CFBD PPA is retained only as a benchmark; the app does not label it as its own EPA.'</n        '</div></section>'
    )


def install_postgame_display(app) -> None:
    if app.extensions.get("postgame_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["postgame_analysis"] = (
        lambda game, team_stats, player_stats: _render(
            repository, dict(game), list(team_stats or ()), list(player_stats or ())
        )
    )
    app.jinja_loader = _PostgameLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["postgame_display_installed"] = True
