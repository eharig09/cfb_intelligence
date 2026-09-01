"""Presentation of private CFBDepth exports inside the ordinary pages.

The upload flow moved to `data_import`, which shows what each source
currently holds beside the control that replaces it.
"""

from __future__ import annotations

from html import escape
from typing import Any

from flask import current_app
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.cfbdepth_data import (
    player_updates,
    roster_breakdown,
    team_impact,
)


TEAM_FACTS_END = '</div>\n\n<nav class="mobile-page-tabs"'
TEAM_FACTS_REPLACEMENT = '</div>\n{{ cfbdepth_roster_strip(team.school) }}\n\n<nav class="mobile-page-tabs"'
GAME_SITUATION_ANCHOR = '    <div class="situation">\n'
GAME_SITUATION_INSERT = '    <div class="situation">\n        {{ cfbdepth_situation_cards(game.away_team, game.home_team) }}\n'
PLAYER_NEWS_ANCHOR = '            <div class="section-note">Stories in which this player was resolved by exact name.</div>\n'
PLAYER_NEWS_INSERT = (
    '            <div class="section-note">Stories in which this player was resolved by exact name.</div>\n'
    '            {{ cfbdepth_player_update_cards(player.name, player.team) }}\n'
)
STYLE_ANCHOR = '\n</style>'
STYLE_INSERT = '''
    .cfbdepth-strip { margin: 8px 0 14px; padding: 7px 10px; border: 1px solid var(--line); background: var(--paper); }
    .cfbdepth-strip-head { display:flex; justify-content:space-between; gap:8px; align-items:baseline; margin-bottom:5px; }
    .cfbdepth-strip-head strong { font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; }
    .cfbdepth-strip-head span { color:var(--muted); font-size:.58rem; }
    .cfbdepth-strip-items { display:flex; flex-wrap:wrap; gap:5px 18px; font-size:.68rem; }
    .cfbdepth-strip-items b { font-family:var(--display-font); }
    .cfbdepth-update { margin:8px 0; border-left:3px solid var(--rust); }
    .cfbdepth-update .status { color:var(--rust); font-weight:900; text-transform:uppercase; letter-spacing:.05em; font-size:.58rem; }
    .cfbdepth-update p { margin:.3rem 0 0; font-size:.75rem; line-height:1.35; }
    .cfbdepth-private { color:var(--muted); font-size:.57rem; }

</style>'''


class _CFBDepthTemplateLoader(BaseLoader):
    def __init__(self, wrapped: BaseLoader):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_team.html" and "cfbdepth_roster_strip(" not in source:
            source = source.replace(TEAM_FACTS_END, TEAM_FACTS_REPLACEMENT, 1)
        elif template == "cfb_game.html" and "cfbdepth_situation_cards(" not in source:
            source = source.replace(GAME_SITUATION_ANCHOR, GAME_SITUATION_INSERT, 1)
        elif template == "cfb_player.html" and "cfbdepth_player_update_cards(" not in source:
            source = source.replace(PLAYER_NEWS_ANCHOR, PLAYER_NEWS_INSERT, 1)
        if template in {"cfb_team.html", "cfb_game.html", "cfb_player.html"} and ".cfbdepth-strip" not in source:
            source = source.replace(STYLE_ANCHOR, STYLE_INSERT, 1)
        return source, filename, uptodate

    def list_templates(self):
        if hasattr(self.wrapped, "list_templates"):
            return self.wrapped.list_templates()
        return []


def _fmt(value: Any, digits: int = 0) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
        return f"{number:.{digits}f}"
    except (TypeError, ValueError):
        return escape(str(value))


def _roster_strip(repository, school: str) -> Markup:
    row = roster_breakdown(repository, school)
    if not row:
        return Markup("")
    items = [
        ("Active", _fmt(row.get("active_players"))),
        ("Transfers", f"{_fmt(row.get('transfers'))} ({_fmt(row.get('transfer_pct'))}%)"),
        ("Home grown", f"{_fmt(row.get('home_grown'))} ({_fmt(row.get('home_grown_pct'))}%)"),
        ("Blue chip", f"{_fmt(row.get('blue_chip_pct'), 1)}%"),
        ("5★ / 4★", f"{_fmt(row.get('five_star'))} / {_fmt(row.get('four_star'))}"),
        ("OL / DL wt", f"{_fmt(row.get('ol_avg_wt'), 1)} / {_fmt(row.get('dl_avg_wt'), 1)}"),
    ]
    body = "".join(f"<span>{escape(label)} <b>{value}</b></span>" for label, value in items)
    return Markup(
        '<div class="cfbdepth-strip">'
        '<div class="cfbdepth-strip-head"><strong>Roster breakdown</strong>'
        '<span>private CFBDepth export</span></div>'
        f'<div class="cfbdepth-strip-items">{body}</div></div>'
    )


def _situation_cards(repository, away: str, home: str) -> Markup:
    cards = []
    for school in (away, home):
        row = team_impact(repository, school)
        if not row:
            continue
        cards.append(
            '<article class="situation-card">'
            f'<h3>{escape(school)} availability impact</h3>'
            f'<p><strong>{_fmt(row.get("injury_impact"), 1)}</strong> team impact '
            f'· {_fmt(row.get("injury_number"))} listed updates</p>'
            f'<div class="meta">Impact/player {_fmt(row.get("impact_pp"), 1)} · '
            'private CFBDepth export</div>'
            '</article>'
        )
    return Markup("".join(cards))


def _player_update_cards(repository, name: str, team: str) -> Markup:
    rows = player_updates(repository, name, team)
    if not rows:
        return Markup("")
    rendered = []
    for row in rows:
        status = escape(str(row.get("status") or "Update"))
        when = escape(str(row.get("last_update") or ""))
        text = escape(str(row.get("update_text") or "").strip())
        text_html = f"<p>{text}</p>" if text else ""
        rendered.append(
            '<article class="card cfbdepth-update">'
            f'<div class="story-head"><span class="status">{status}</span><span class="meta">{when}</span></div>'
            f'{text_html}'
            '<div class="cfbdepth-private">CFBDepth player update · private imported data</div>'
            '</article>'
        )
    return Markup("".join(rendered))


def install_cfbdepth_display(app) -> None:
    if app.extensions.get("cfbdepth_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["cfbdepth_roster_strip"] = lambda school: _roster_strip(repository, school)
    app.jinja_env.globals["cfbdepth_situation_cards"] = lambda away, home: _situation_cards(repository, away, home)
    app.jinja_env.globals["cfbdepth_player_update_cards"] = lambda name, team: _player_update_cards(repository, name, team)
    app.jinja_loader = _CFBDepthTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()

    app.extensions["cfbdepth_display_installed"] = True
