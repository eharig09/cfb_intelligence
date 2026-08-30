"""Presentation and protected upload flow for private CFBDepth exports."""

from __future__ import annotations

from html import escape
import secrets
from typing import Any

from flask import Blueprint, current_app, render_template_string, request, session
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.cfbdepth_data import (
    import_player_updates,
    import_roster_breakdown,
    import_team_impact,
    player_updates,
    roster_breakdown,
    team_impact,
)
from sports_aggregator.cfb.cfbdepth_flexible import canonicalize_cfbdepth_upload
from sports_aggregator.page_cache import cache


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


IMPORT_PAGE = """
{% extends "_layout.html" %}
{% block title %}CFBDepth Import | College Football{% endblock %}
{% block content %}
<section class="section" style="max-width:760px;margin:auto">
  <div class="eyebrow">Private data admin</div>
  <h1>Import CFBDepth exports</h1>
  <div class="section-note">Files are preflighted and header-normalized before any snapshot table is replaced. The files are parsed into the persistent CFB SQLite database and are not written into the Git repository.</div>
  {% if message %}<article class="card"><strong>{{ message }}</strong></article>{% endif %}
  <form method="post" enctype="multipart/form-data" style="display:grid;gap:12px">
    <label>Admin PIN or refresh token<br><input type="password" name="token" required style="width:100%"></label>
    <label>Roster Breakdown CSV<br><input type="file" name="roster" accept=".csv,text/csv"></label>
    <label>Team Impact Report CSV<br><input type="file" name="impact" accept=".csv,text/csv"></label>
    <label>Player Updates CSV<br><input type="file" name="updates" accept=".csv,text/csv"></label>
    <button type="submit">Validate and import selected exports</button>
  </form>
</section>
{% endblock %}
"""


def _authorized() -> bool:
    if session.get("cfb_admin") is True:
        return True
    supplied = str(request.form.get("token") or "").strip()
    if not supplied:
        return False
    for key in ("CFB_ADMIN_PIN", "CFB_REFRESH_TOKEN"):
        expected = str(current_app.config.get(key) or "").strip()
        if expected and secrets.compare_digest(supplied, expected):
            return True
    return False


def _safe_upload(file_storage, expected_kind: str) -> tuple[str, str] | None:
    if not file_storage or not file_storage.filename:
        return None
    raw = file_storage.read()
    check, canonical = canonicalize_cfbdepth_upload(
        raw, expected_kind=expected_kind, label=file_storage.filename
    )
    optional_note = (
        f"; optional columns absent: {', '.join(check.missing_optional)}"
        if check.missing_optional else ""
    )
    return canonical, optional_note


def install_cfbdepth_display(app) -> None:
    if app.extensions.get("cfbdepth_display_installed"):
        return
    repository = app.extensions["cfb_repository"]
    app.jinja_env.globals["cfbdepth_roster_strip"] = lambda school: _roster_strip(repository, school)
    app.jinja_env.globals["cfbdepth_situation_cards"] = lambda away, home: _situation_cards(repository, away, home)
    app.jinja_env.globals["cfbdepth_player_update_cards"] = lambda name, team: _player_update_cards(repository, name, team)
    app.jinja_loader = _CFBDepthTemplateLoader(app.jinja_loader)
    app.jinja_env.cache.clear()

    blueprint = Blueprint("cfbdepth_private", __name__)

    @blueprint.route("/college-football/cfbdepth-import/", methods=["GET", "POST"])
    def import_page():
        message = None
        if request.method == "POST":
            if not _authorized():
                return render_template_string(IMPORT_PAGE, message="Authorization failed."), 401
            try:
                prepared = {
                    "roster": _safe_upload(request.files.get("roster"), "roster"),
                    "impact": _safe_upload(request.files.get("impact"), "impact"),
                    "updates": _safe_upload(request.files.get("updates"), "updates"),
                }
            except ValueError as exc:
                # Crucially, no importer has been called yet, so the current
                # production snapshots remain untouched when preflight fails.
                return render_template_string(
                    IMPORT_PAGE, message=f"Validation failed — existing data was not changed. {exc}"
                ), 400

            counts = []
            warnings = []
            if prepared["roster"]:
                canonical, note = prepared["roster"]
                counts.append(f"roster={import_roster_breakdown(repository, canonical)}")
                if note:
                    warnings.append("roster" + note)
            if prepared["impact"]:
                canonical, note = prepared["impact"]
                counts.append(f"impact={import_team_impact(repository, canonical)}")
                if note:
                    warnings.append("impact" + note)
            if prepared["updates"]:
                canonical, note = prepared["updates"]
                counts.append(f"updates={import_player_updates(repository, canonical)}")
                if note:
                    warnings.append("updates" + note)
            cache.clear()
            message = "Imported " + ", ".join(counts) if counts else "No CSV files selected."
            if warnings:
                message += " Warnings: " + " | ".join(warnings)
        return render_template_string(IMPORT_PAGE, message=message)

    app.register_blueprint(blueprint)
    app.extensions["cfbdepth_display_installed"] = True
