"""Small presentation refinements for imported CFBDepth data.

This layer intentionally sits outside the core CFBD repository. It reshapes the
private roster export into the same fact-card language as the team page and
surfaces current-roster players with imported availability updates on matchup
pages.
"""

from __future__ import annotations

from contextlib import closing
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.cfbdepth_data import initialize, roster_breakdown
from sports_aggregator.cfb.depth_chart_observed_display import install_observed_depth_display
from sports_aggregator.cfb.depth_chart_profiles import install_depth_chart_profiles
from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.production_display import install_production_display


ROSTER_CALL = "{{ cfbdepth_roster_strip(team.school) }}"
ROSTER_REPLACEMENT = "{{ cfbdepth_roster_facts(team.school) }}"
PLAYER_MATCHUP_ANCHOR = "    {{ data_table(player_matchup_table) }}\n"
PLAYER_MATCHUP_REPLACEMENT = (
    "    {{ cfbdepth_matchup_player_flags(game.away_team, game.home_team, game.season) }}\n"
    "    {{ data_table(player_matchup_table) }}\n"
)
STYLE_ANCHOR = "\n</style>"
STYLE_INSERT = '''
    .cfbdepth-roster-note {
        display:flex; justify-content:space-between; gap:8px; margin:8px 0 4px;
        color:var(--muted); font-size:.58rem; text-transform:uppercase; letter-spacing:.055em;
    }
    .cfbdepth-roster-facts { margin-bottom:14px; }
    @media (min-width:860px) {
        .cfbdepth-roster-facts { grid-template-columns:repeat(7,minmax(0,1fr)); }
        .cfbdepth-roster-facts .fact { min-width:0; padding-inline:8px; }
        .cfbdepth-roster-facts .fact b { font-size:1.05rem; white-space:nowrap; }
        .cfbdepth-roster-facts .fact span { font-size:.54rem; line-height:1.2; }
    }
    .cfbdepth-player-flags {
        display:flex; flex-wrap:wrap; gap:6px 8px; margin:10px 0 12px;
    }
    .cfbdepth-player-flag {
        display:inline-flex; gap:5px; align-items:baseline; padding:5px 8px;
        border:1px solid var(--line); border-left:3px solid var(--rust);
        background:var(--paper); font-size:.67rem; cursor:help;
    }
    .cfbdepth-player-flag a { font-weight:800; text-decoration:none; }
    .cfbdepth-player-flag .status {
        color:var(--rust); font-size:.56rem; font-weight:900;
        text-transform:uppercase; letter-spacing:.045em;
    }
    .cfbdepth-player-flag .impact {
        font-size:.58rem; font-weight:900; font-variant-numeric:tabular-nums;
        color:var(--ink); white-space:nowrap;
    }
    .cfbdepth-player-flag .meta { font-size:.58rem; }
    .cfbdepth-player-flags-label {
        width:100%; color:var(--muted); font-size:.58rem; text-transform:uppercase;
        letter-spacing:.055em; font-weight:800;
    }

</style>'''


class _EnhancementLoader(BaseLoader):
    def __init__(self, wrapped: BaseLoader):
        self.wrapped = wrapped

    def get_source(self, environment, template):
        if self.wrapped is None:
            raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_team.html" and "cfbdepth_roster_facts(" not in source:
            source = source.replace(ROSTER_CALL, ROSTER_REPLACEMENT, 1)
        if template == "cfb_game.html" and "cfbdepth_matchup_player_flags(" not in source:
            source = source.replace(PLAYER_MATCHUP_ANCHOR, PLAYER_MATCHUP_REPLACEMENT, 1)
        if template in {"cfb_team.html", "cfb_game.html"} and ".cfbdepth-roster-facts" not in source:
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
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return escape(str(value))


def _roster_facts(repository, school: str) -> Markup:
    row = roster_breakdown(repository, school)
    if not row:
        return Markup("")
    facts = [
        (_fmt(row.get("active_players")), "Active players"),
        (f"{_fmt(row.get('transfers'))} · {_fmt(row.get('transfer_pct'))}%", "Transfers"),
        (f"{_fmt(row.get('home_grown'))} · {_fmt(row.get('home_grown_pct'))}%", "Home grown"),
        (f"{_fmt(row.get('blue_chip_pct'), 1)}%", "Blue chip"),
        (f"{_fmt(row.get('five_star'))} / {_fmt(row.get('four_star'))}", "5★ / 4★"),
        (f"{_fmt(row.get('ol_avg_wt'), 1)}", "OL avg weight"),
        (f"{_fmt(row.get('dl_avg_wt'), 1)}", "DL avg weight"),
    ]
    cards = "".join(
        f'<div class="fact"><b>{value}</b><span>{escape(label)}</span></div>'
        for value, label in facts
    )
    return Markup(
        '<div class="cfbdepth-roster-note"><strong>Roster breakdown</strong>'
        '<span>private CFBDepth export</span></div>'
        f'<div class="facts cfbdepth-roster-facts">{cards}</div>'
    )


def _matchup_updates(repository, away: str, home: str, season: int) -> list[dict[str, Any]]:
    """Latest imported availability row for current-roster players only."""
    initialize(repository)
    team_lookup = {
        normalize_alias(away): away,
        normalize_alias(home): home,
    }
    with closing(repository._connect()) as connection:
        source_rows = [dict(row) for row in connection.execute(
            """SELECT * FROM cfbdepth_player_updates
               WHERE normalized_team IN (?,?)
               ORDER BY update_id DESC""",
            tuple(team_lookup.keys()),
        ).fetchall()]
        roster_rows = [dict(row) for row in connection.execute(
            """SELECT player_id,first_name,last_name,normalized_name,team,position
               FROM players WHERE season=? AND team IN (?,?)""",
            (int(season), away, home),
        ).fetchall()]

    roster = {
        (str(row["normalized_name"]), normalize_alias(str(row["team"]))): row
        for row in roster_rows
    }
    seen: set[tuple[str, str]] = set()
    matched: list[dict[str, Any]] = []
    for update in source_rows:
        key = (str(update["normalized_name"]), str(update["normalized_team"]))
        if key in seen or key not in roster:
            continue
        seen.add(key)
        player = roster[key]
        matched.append({
            **update,
            "player_id": player["player_id"],
            "display_position": player.get("position") or update.get("position"),
            "display_team": player["team"],
        })
    status_order = {"Out for Season": 0, "Out": 1, "Doubtful": 2, "Questionable": 3, "Probable": 4}
    matched.sort(key=lambda row: (
        status_order.get(str(row.get("status") or ""), 9),
        -(float(row.get("impact")) if row.get("impact") is not None else -1.0),
        str(row.get("display_team") or ""),
        str(row.get("player_name") or ""),
    ))
    return matched


def _matchup_flags(repository, away: str, home: str, season: int) -> Markup:
    rows = _matchup_updates(repository, away, home, season)
    if not rows:
        return Markup("")
    flags = []
    for row in rows:
        href = url_for("cfb.player_preview", player_id=row["player_id"])
        name = escape(str(row.get("player_name") or ""))
        status = escape(str(row.get("status") or "Update"))
        position = escape(str(row.get("display_position") or "—"))
        team = escape(str(row.get("display_team") or ""))
        impact = _fmt(row.get("impact"), 1)
        description = str(row.get("update_text") or "").strip()
        last_update = str(row.get("last_update") or "").strip()
        tooltip_parts = [part for part in (description, f"Updated {last_update}" if last_update else "") if part]
        tooltip = escape(" — ".join(tooltip_parts), quote=True)
        title_attr = f' title="{tooltip}"' if tooltip else ""
        flags.append(
            f'<span class="cfbdepth-player-flag"{title_attr}>'
            f'<a href="{href}">{name}</a>'
            f'<span class="status">{status}</span>'
            f'<span class="impact">Impact {impact}</span>'
            f'<span class="meta">{team} · {position}</span>'
            '</span>'
        )
    return Markup(
        '<div class="cfbdepth-player-flags">'
        '<div class="cfbdepth-player-flags-label">Player availability connections · private CFBDepth export · hover for update detail</div>'
        + "".join(flags)
        + '</div>'
    )


def install_cfbdepth_enhancements(app) -> None:
    if app.extensions.get("cfbdepth_enhancements_installed"):
        return
    repository = app.extensions["cfb_repository"]
    install_depth_chart_profiles()
    install_observed_depth_display(repository)
    install_production_display(app)
    app.jinja_env.globals["cfbdepth_roster_facts"] = (
        lambda school: _roster_facts(repository, str(school))
    )
    app.jinja_env.globals["cfbdepth_matchup_player_flags"] = (
        lambda away, home, season: _matchup_flags(
            repository, str(away), str(home), int(season)
        )
    )
    app.jinja_loader = _EnhancementLoader(app.jinja_loader)
    app.jinja_env.cache.clear()
    app.extensions["cfbdepth_enhancements_installed"] = True
