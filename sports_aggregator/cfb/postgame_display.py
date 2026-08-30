"""Render the evidence-based postgame report above the raw box score."""

from __future__ import annotations

from contextlib import closing
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.postgame import postgame_report


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
.postgame-factor,.postgame-player,.postgame-role{border:1px solid var(--line);background:var(--paper);padding:11px 12px}
.postgame-factor strong,.postgame-player strong,.postgame-role strong{display:block;font-size:.78rem;margin-bottom:4px}.postgame-factor p,.postgame-player p,.postgame-role p{margin:0;font-size:.7rem;line-height:1.4}
.postgame-factor .factor-head{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:5px}.postgame-factor .rank{font-family:var(--display-font);font-size:1.15rem}.postgame-sub{color:var(--muted);font-size:.58rem;text-transform:uppercase;letter-spacing:.045em}.postgame-coverage{border-left:3px solid var(--line);padding:8px 11px;color:var(--muted);font-size:.66rem;line-height:1.45;margin-top:12px}
.postgame-section-head{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin-top:20px}.postgame-section-head h3{margin:0}.postgame-section-head span{color:var(--muted);font-size:.58rem}
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


def _render(repository, game: dict[str, Any], team_stats, player_stats) -> Markup:
    report = postgame_report(repository, game, team_stats or (), player_stats or ())
    season = int(game.get("season") or 0)
    _role_names(repository, season, report["roles"])

    factor_cards = []
    for index, factor in enumerate(report["factors"], 1):
        factor_cards.append(
            '<article class="postgame-factor">'
            '<div class="factor-head">'
            f'<span class="rank">{index}</span>'
            f'<span class="postgame-sub">{escape(str(factor.get("confidence") or ""))} confidence</span>'
            '</div>'
            f'<strong>{escape(str(factor.get("headline") or factor.get("label") or "Factor"))}</strong>'
            f'<p>{escape(str(factor.get("detail") or ""))}</p>'
            f'<div class="postgame-sub" style="margin-top:6px">{escape(str(factor.get("source") or ""))}</div>'
            '</article>'
        )
    factors_html = "".join(factor_cards) or (
        '<div class="empty">The stored box score does not yet expose a statistically distinct decisive factor.</div>'
    )

    player_cards = []
    for row in report["players"]:
        href = _player_url(row.get("player_id"), season)
        name = escape(str(row.get("player") or "Player"))
        name_html = f'<a href="{href}">{name}</a>' if href else name
        player_cards.append(
            '<article class="postgame-player">'
            f'<div class="postgame-sub">{escape(str(row.get("team") or ""))}</div>'
            f'<strong>{name_html}</strong>'
            f'<p>{escape(str(row.get("summary") or ""))}</p>'
            '</article>'
        )
    players_html = "".join(player_cards) or (
        '<div class="empty">No player-level impact rows are stored for this game yet.</div>'
    )

    role_cards = []
    for row in report["roles"]:
        href = _player_url(row.get("player_id"), season)
        name = escape(str(row.get("player_name") or "Current roster player"))
        name_html = f'<a href="{href}">{name}</a>' if href else name
        week = row.get("latest_week")
        through = f" through Week {week}" if week is not None else ""
        role_cards.append(
            '<article class="postgame-role">'
            f'<div class="postgame-sub">{escape(str(row.get("team") or ""))} · {escape(str(row.get("position") or ""))}</div>'
            f'<strong>{name_html}</strong>'
            f'<p>Observed #{int(row.get("observed_rank") or 0)} in recent game usage · '
            f'{int(row.get("games") or 0)} games{escape(through)} · '
            f'{escape(str(row.get("confidence") or "early"))} confidence.</p>'
            '</article>'
        )
    roles_html = "".join(role_cards) or (
        '<div class="empty">No multi-game role change has cleared the observed-depth threshold yet.</div>'
    )

    coverage = report["coverage"]
    return Markup(
        STYLE +
        '<section class="section postgame-shell">'
        '<div class="eyebrow">Postgame intelligence</div>'
        '<h2>Game story & analysis</h2>'
        f'<p class="postgame-lede">{escape(str(report["story"]))}</p>'
        '<div class="postgame-meta">'
        f'<span class="postgame-tag">{escape(str(report["complexion"]))}</span>'
        f'<span class="postgame-tag">Margin {float(report["margin"]):g}</span>'
        f'<span class="postgame-tag">{len(report["factors"])} measurable separators</span>'
        '</div>'
        '<div class="postgame-section-head"><h3>What decided it</h3><span>Ranked from stored evidence</span></div>'
        f'<div class="postgame-grid">{factors_html}</div>'
        '<div class="postgame-section-head"><h3>Player impact</h3><span>Box-score production, not a subjective grade</span></div>'
        f'<div class="postgame-grid">{players_html}</div>'
        '<div class="postgame-section-head"><h3>What may have changed</h3><span>Current-roster observed role signal</span></div>'
        f'<div class="postgame-grid">{roles_html}</div>'
        '<div class="postgame-coverage"><strong>Analysis coverage.</strong> '
        f'{escape(str(coverage.get("coverage_note") or ""))} '
        'A factor is omitted when the underlying field is absent or the difference is too small to support a meaningful claim.'
        '</div></section>'
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
