"""Render precomputed quarterback air-yard summaries in postgame reports."""
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.qb_air_yards import game_summary, METRIC_VERSION, MODEL_VERSION

ANCHOR = "{{ postgame_tendencies(game) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_qb_air_yards(game) }}"
BACKUP_SHARE_MIN = 0.20
BACKUP_PLAYS_MIN = 8

STYLE = '''<style>
.pg-qb-air{margin:0 0 18px}.pg-qb-air-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-end;margin:17px 0 7px;padding-bottom:6px;border-bottom:1px solid var(--line)}.pg-qb-air-head h3{margin:0;font-size:.8rem}.pg-qb-air-head span{font-size:.51rem;color:var(--muted)}
.pg-qb-air-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.pg-qb-team{min-width:0}.pg-qb-team-head{margin:0 0 5px;font-size:.57rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:850}.pg-qb-card{border-top:2px solid var(--line);padding:9px 0 10px;min-width:0}.pg-qb-card.backup{border-top:1px solid var(--line);padding-top:8px}.pg-qb-card h4{margin:0 0 7px;font-size:.72rem}.pg-qb-card.backup h4{font-size:.65rem}.pg-qb-card h4 a{text-decoration:none}.pg-qb-role{color:var(--muted);font-size:.48rem;text-transform:uppercase;letter-spacing:.05em;margin-left:5px}.pg-qb-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.pg-qb-metric span{display:block;font-size:.45rem;text-transform:uppercase;letter-spacing:.055em;color:var(--muted);font-weight:800}.pg-qb-metric strong{display:block;margin-top:2px;font-family:var(--display-font);font-size:.75rem;font-variant-numeric:tabular-nums}.pg-qb-card.backup .pg-qb-metric strong{font-size:.68rem}.pg-qb-depth{margin-top:8px;padding-top:7px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:5px 10px;font-size:.53rem}.pg-qb-depth strong{font-variant-numeric:tabular-nums}.pg-qb-note{margin:7px 0 0;color:var(--muted);font-size:.52rem;line-height:1.45}
@media(max-width:760px){.pg-qb-air-grid{grid-template-columns:1fr}.pg-qb-air-head span{display:none}}@media(max-width:480px){.pg-qb-metrics{grid-template-columns:1fr 1fr}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self, wrapped): self.wrapped = wrapped
    def get_source(self, environment, template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source, filename, uptodate = self.wrapped.get_source(environment, template)
        if template == "cfb_box_score.html" and "postgame_qb_air_yards(" not in source and ANCHOR in source:
            source = source.replace(ANCHOR, REPLACEMENT, 1)
        return source, filename, uptodate
    def list_templates(self): return self.wrapped.list_templates() if hasattr(self.wrapped, "list_templates") else []


def _f1(value: Any, *, signed: bool = False) -> str:
    if value is None: return "—"
    try:
        number = float(value); return f"{number:+.1f}" if signed else f"{number:.1f}"
    except (TypeError, ValueError): return "—"


def _pct(value: Any) -> str:
    if value is None: return "—"
    try: return f"{100 * float(value):.0f}%"
    except (TypeError, ValueError): return "—"


def _card(row: dict[str, Any], *, backup: bool = False, share: float | None = None) -> str:
    name = escape(str(row.get("player_name") or "Quarterback")); player_id = row.get("player_id"); href = url_for("cfb.player_preview", player_id=player_id, season=row.get("season")) if player_id else None; shown = f'<a href="{href}">{name}</a>' if href else name
    role = f"Backup · {100*share:.0f}% of team pass plays" if backup and share is not None else "Backup"
    metrics = (("Air yards",_f1(row.get("measured_air_yards"))),("Air yds / comp",_f1(row.get("measured_adot"))),("YAC",_f1(row.get("yards_after_catch"))),("Pass EPA",_f1(row.get("pass_epa"),signed=True)),("EPA / attr. pass",_f1(row.get("epa_per_attributed_pass"),signed=True)),("Measured comps",str(int(row.get("measured_completions") or 0))),("Attr. pass plays",str(int(row.get("attributed_pass_plays") or 0))),("Numeric coverage",_pct(row.get("numeric_depth_coverage"))))
    metric_html=''.join(f'<div class="pg-qb-metric"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>' for label,value in metrics)
    depth=(("Behind LOS",row.get("behind_line_plays")),("Short",row.get("short_plays")),("Intermediate",row.get("intermediate_plays")),("Deep",row.get("deep_plays")))
    depth_html=' · '.join(f'{escape(label)} <strong>{int(value or 0)}</strong>' for label,value in depth)
    # Hoisted rather than nested inline: escaping quotes inside an f-string
    # expression is 3.12-and-later syntax, and it is the only thing in the tree
    # that stops the app importing on 3.11.
    role_html = f'<span class="pg-qb-role">{escape(role)}</span>' if backup else ""
    return f'<article class="pg-qb-card{" backup" if backup else ""}"><h4>{shown}{role_html}</h4><div class="pg-qb-metrics">{metric_html}</div><div class="pg-qb-depth">{depth_html}</div></article>'


def _team_column(team: str, rows: list[dict[str, Any]]) -> str:
    rows=sorted(rows,key=lambda r:(-int(r.get("attributed_pass_plays") or 0),str(r.get("player_name") or "")))
    if not rows:return f'<section class="pg-qb-team"><div class="pg-qb-team-head">{escape(team)}</div><div class="empty">No attributed quarterback passing plays.</div></section>'
    total=sum(int(r.get("attributed_pass_plays") or 0) for r in rows); cards=[_card(rows[0])]
    for backup in rows[1:]:
        plays=int(backup.get("attributed_pass_plays") or 0); share=plays/total if total else 0.0
        if plays>=BACKUP_PLAYS_MIN and share>=BACKUP_SHARE_MIN: cards.append(_card(backup,backup=True,share=share))
    return f'<section class="pg-qb-team"><div class="pg-qb-team-head">{escape(team)}</div>{"".join(cards)}</section>'


def _render(repository, game: dict[str, Any]) -> Markup:
    try: rows=game_summary(repository,int(game.get("game_id") or 0))
    except Exception: rows=[]
    if not rows:return Markup("")
    grouped=defaultdict(list)
    for row in rows: grouped[str(row.get("team") or "Team")].append(row)
    away=str(game.get("away_team") or "Away"); home=str(game.get("home_team") or "Home"); columns=[_team_column(away,grouped.get(away,[])),_team_column(home,grouped.get(home,[]))]
    return Markup(STYLE+'<section class="pg-qb-air">'+f'<div class="pg-qb-air-head"><h3>Quarterback air yards</h3><span>{escape(METRIC_VERSION)} · play-detail-v3 × {escape(MODEL_VERSION)}</span></div>'+f'<div class="pg-qb-air-grid">{"".join(columns)}</div>'+'<p class="pg-qb-note">Primary quarterbacks are paired for direct comparison. Backups appear only when they account for at least 20% of attributed team pass plays and at least 8 plays. Air yards and YAC are measured only on completions with an unambiguous catch spot; “Air yds / comp” is not full aDOT.</p></section>')


def install_qb_air_yards_display(app) -> None:
    if app.extensions.get("qb_air_yards_display_installed"): return
    repository=app.extensions["cfb_repository"]; app.jinja_env.globals["postgame_qb_air_yards"]=lambda game:_render(repository,dict(game)); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions["qb_air_yards_display_installed"]=True
