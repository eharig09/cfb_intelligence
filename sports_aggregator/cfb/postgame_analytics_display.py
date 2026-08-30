"""Supplement the postgame report with compact pace/game-state and WP turning points."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.identity import team_identity
from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.wp_turning_points import EPA_MODEL_VERSION, game_turning_points

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_pace_and_leverage(game) }}"
WP_MODEL_VERSION = "wp-v2"

STYLE = '''<style>
.pg-analytics{margin:0 0 28px}.pg-section-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin:22px 0 9px;padding-bottom:7px;border-bottom:1px solid var(--line)}.pg-section-head h3{margin:0;font-size:.9rem}.pg-section-head span{font-size:.56rem;color:var(--muted)}
.pg-summary-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}.pg-summary-card{border:1px solid var(--line);background:var(--paper);padding:10px 12px}.pg-summary-card h4{margin:0 0 8px;font-size:.68rem}.pg-summary-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.pg-summary-metric span{display:block;font-size:.48rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:800}.pg-summary-metric strong{display:block;margin-top:3px;font-family:var(--display-font);font-size:.82rem}
.pg-details{border:1px solid var(--line);background:var(--paper);margin:8px 0 5px}.pg-details summary{cursor:pointer;padding:8px 11px;font-size:.56rem;font-weight:800;text-transform:uppercase;letter-spacing:.055em;color:var(--muted)}.pg-details[open] summary{border-bottom:1px solid var(--line)}.pg-detail-grid{display:grid;grid-template-columns:1fr 1fr}.pg-detail-team{padding:9px 11px}.pg-detail-team+.pg-detail-team{border-left:1px solid var(--line)}.pg-detail-team h4{margin:0 0 6px;font-size:.64rem}.pg-row{display:grid;grid-template-columns:1.35fr .72fr .72fr;gap:8px;padding:4px 0;border-top:1px solid var(--line);font-size:.57rem}.pg-row:first-of-type{border-top:0}.pg-num{text-align:right}.pg-note{color:var(--muted);font-size:.55rem;line-height:1.45;margin:7px 0 0}
.pg-turning{border-top:1px solid var(--line);margin:10px 0 4px}.pg-turn{display:grid;grid-template-columns:42px minmax(285px,.9fr) minmax(0,1.45fr);gap:15px;padding:15px 4px;border-bottom:1px solid var(--line);align-items:start}.pg-turn-rank{font-family:var(--display-font);font-size:1.18rem;line-height:1;color:var(--team-light)}.pg-turn-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.pg-turn-clock{font-family:var(--display-font);font-size:.78rem;font-weight:850}.pg-turn-event{font-size:.5rem;text-transform:uppercase;letter-spacing:.065em;font-weight:900;color:var(--team-light);border:1px solid color-mix(in srgb,var(--team-light) 55%,var(--line));padding:3px 6px}.pg-turn-swing{margin-top:6px;font-family:var(--display-font);font-size:.9rem}.pg-turn-swing strong{font-size:1rem}.pg-turn-wp{margin-top:4px;font-size:.66rem;font-weight:800}.pg-turn-context{margin-top:8px;font-size:.65rem;line-height:1.5;color:var(--muted)}.pg-turn-play{margin:0;font-size:.72rem;line-height:1.55;font-weight:700}.pg-turn-player{font-weight:950}.pg-turn-meta{margin-top:6px;color:var(--muted);font-size:.54rem;line-height:1.4}.pg-turn-attribution{margin-top:6px;font-size:.54rem;color:var(--muted);font-style:italic}
.box-report .efficiency-best{display:none!important}.box-report .efficiency-cell.edge{box-shadow:none!important;background:transparent!important}
@media(max-width:820px){.pg-turn{grid-template-columns:34px 1fr}.pg-turn-detail{grid-column:2}.pg-summary-grid,.pg-detail-grid{grid-template-columns:1fr}.pg-detail-team+.pg-detail-team{border-left:0;border-top:1px solid var(--line)}.pg-section-head span{display:none}}
</style>'''


class _Loader(BaseLoader):
    def __init__(self,wrapped): self.wrapped=wrapped
    def get_source(self,environment,template):
        if self.wrapped is None: raise TemplateNotFound(template)
        source,filename,uptodate=self.wrapped.get_source(environment,template)
        if template=="cfb_box_score.html" and "postgame_pace_and_leverage(" not in source: source=source.replace(ANCHOR,REPLACEMENT,1)
        return source,filename,uptodate
    def list_templates(self): return self.wrapped.list_templates() if hasattr(self.wrapped,"list_templates") else []


def _pct(v): return "—" if v is None else f"{100*float(v):.1f}%"
def _rate(v): return "—" if v is None else f"{float(v):.2f}/min"


def _summary_card(team,states):
    overall=states.get("overall") or {}; neutral=states.get("neutral") or {}; passing=states.get("passing_downs") or {}
    return f'<article class="pg-summary-card"><h4>{escape(team)}</h4><div class="pg-summary-metrics"><div class="pg-summary-metric"><span>Overall tempo</span><strong>{escape(_rate(overall.get("play_rate")))}</strong></div><div class="pg-summary-metric"><span>Neutral pass rate</span><strong>{escape(_pct(neutral.get("pass_rate")))}</strong></div><div class="pg-summary-metric"><span>Passing-down pass</span><strong>{escape(_pct(passing.get("pass_rate")))}</strong></div></div></article>'


def _detail_team(team,states):
    labels=(("overall","Overall"),("neutral","Neutral"),("leading","Leading"),("trailing","Trailing"),("leading_one_score","Lead ≤8"),("leading_multi_score","Lead 9+"),("trailing_one_score","Trail ≤8"),("trailing_multi_score","Trail 9+"),("standard_downs","Standard downs"),("passing_downs","Passing downs")); rows=[]
    for key,label in labels:
        row=states.get(key)
        if row and row.get("plays"): rows.append(f'<div class="pg-row"><span>{escape(label)} <small>({int(row.get("plays") or 0)})</small></span><span class="pg-num">{escape(_rate(row.get("play_rate")))}</span><span class="pg-num">{escape(_pct(row.get("pass_rate")))}</span></div>')
    return f'<section class="pg-detail-team"><h4>{escape(team)}</h4><div class="pg-row"><strong>Situation</strong><strong class="pg-num">Tempo</strong><strong class="pg-num">Pass</strong></div>{"".join(rows)}</section>'


def _down_distance(row):
    try: down=int(row.get("down")); distance=int(row.get("distance"))
    except (TypeError,ValueError): return ""
    return f"{down}{ {1:'st',2:'nd',3:'rd'}.get(down,'th') } & {distance}"


def _field_position(row):
    offense=str(row.get("offense") or ""); defense=str(row.get("defense") or "")
    try: ytg=int(row.get("yards_to_goal"))
    except (TypeError,ValueError): return ""
    return f"at the {defense} {ytg}" if ytg<=50 and defense else (f"at the {offense} {100-ytg}" if offense else "")


def _scoreline(row,game):
    if row.get("home_score") is None or row.get("away_score") is None:return ""
    return f"{game.get('away_team') or 'Away'} {row.get('away_score')} · {game.get('home_team') or 'Home'} {row.get('home_score')}"


def _event_label(row):
    text=f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    if "touchdown" in text and "intercept" in text:return "Pick-six"
    if "touchdown" in text and "fumble" in text:return "Fumble TD"
    if "touchdown" in text:return "Touchdown"
    if "intercept" in text:return "Interception"
    if "fumble" in text:return "Fumble"
    if "field goal" in text:return "Field goal"
    if "safety" in text:return "Safety"
    if int(row.get("down") or 0)==4:return "Fourth down"
    return ""


def _event_clock(row):
    vals=[row.get("event_period") if row.get("event_period") is not None else row.get("period"),row.get("event_clock_minutes") if row.get("event_clock_minutes") is not None else row.get("clock_minutes"),row.get("event_clock_seconds") if row.get("event_clock_seconds") is not None else row.get("clock_seconds")]
    try:return int(vals[0] or 0),int(vals[1] or 0),int(vals[2] or 0)
    except (TypeError,ValueError):return 0,0,0


def _team_codes(team):
    letters=re.sub(r"[^A-Z]","",team.upper()); words=re.findall(r"[A-Za-z]+",team); codes={letters[:n] for n in range(2,min(6,len(letters)+1))}
    if len(words)>1:codes.add("".join(w[0] for w in words).upper())
    return {c for c in codes if len(c)>=2}


def _humanize_field_codes(text,game):
    code_map={}
    for team in (str(game.get("away_team") or "Away"),str(game.get("home_team") or "Home")):
        for code in _team_codes(team):code_map[code]=team
    def repl(m):
        team=code_map.get(m.group(1).upper()); yard=int(m.group(2))
        return m.group(0) if not team else (f"{team} goal line" if yard==0 else f"{team} {yard}")
    return re.sub(r"\b([A-Z]{2,6})(\d{2})\b",repl,text)


def _clean_play_text(text,game):
    human=_humanize_field_codes(text,game); human=re.sub(r"^\(\d{1,2}:\d{2}\)\s*","",human).strip(); human=re.sub(r",?\s*clock\s+\d{1,2}:\d{2}","",human,flags=re.I)
    # Providers use both literal TOUCHDOWN and older "for a TD (...)" grammar.
    # Strip appended PAT/two-point/timeout material from either form while
    # preserving the actual scoring play sentence.
    td=re.search(r"\bTOUCHDOWN\b|\bfor a TD\b",human,flags=re.I)
    if td and any(t in human[td.end():].casefold() for t in ("kick attempt","pass attempt","two-point","two point","extra point","timeout")):
        human=human[:td.end()]
    catch=re.search(r"caught at (.+?),\s*for (-?\d+) yards to (?:the )?(.+?)(?=,|$)",human,flags=re.I)
    if catch:
        a=catch.group(1).strip(); y=int(catch.group(2)); b=catch.group(3).strip(); repl=f"caught at {a} — {y}-yard {'gain' if y>=0 else 'loss'}" if a.casefold()==b.casefold() else f"caught at {a}, advanced to {b} — {y}-yard {'gain' if y>=0 else 'loss'}"; human=human[:catch.start()]+repl+human[catch.end():]
    return re.sub(r"\s{2,}"," ",human).strip(" ,")


def _team_colors(repository,game):
    colors={}
    for side in ("away","home"):
        team=str(game.get(f"{side}_team") or ""); team_id=game.get(f"{side}_team_id")
        if not team or team_id is None:continue
        try:
            identity=team_identity(repository.brand_for(int(team_id))); colors[team]=str(identity.get("accent_dark") or identity.get("accent") or "var(--team-light)")
        except Exception:colors[team]="var(--team-light)"
    return colors

# Highlight provider player tokens with or without jersey numbers. The compact
# no-jersey form (e.g. "S. Mikaele") is common in older/summary scoring rows.
_PLAYER=re.compile(r"(#\d+\s+[A-Z][A-Za-z.'’\-]*(?:\s+[A-Z][A-Za-z.'’\-]*)?|(?<![#\w])[A-Z]\.\s*[A-Z][A-Za-z.'’\-]*(?:\s+(?:Jr\.|II|III|IV))?)")
_DEFENSE_CUES=("broken up by","tackled by","sacked by","intercepted by","forced by","recovered by","blocked by","hurried by")
_DEFENSE_CONTEXT=re.compile(r"(?:broken up by|tackled by|sacked by|intercepted by|forced by|recovered by|blocked by|hurried by)(?:\s+[a-z][a-z.'’\-]*){0,2}\s*$",re.I)


def _play_html(text,game,colors,offense,defense):
    human=_clean_play_text(text,game); pieces=[]; last=0
    for match in _PLAYER.finditer(human):
        pieces.append(escape(human[last:match.start()])); before=human[max(0,match.start()-70):match.start()].rstrip(); defender=(match.start()>0 and human[match.start()-1]=="(") or bool(_DEFENSE_CONTEXT.search(before)) or any(before.casefold().endswith(cue) for cue in _DEFENSE_CUES); team=defense if defender else offense; color=colors.get(team,"var(--team-light)"); pieces.append(f'<span class="pg-turn-player" style="color:{escape(color,quote=True)}">{escape(match.group(1))}</span>'); last=match.end()
    pieces.append(escape(human[last:])); return "".join(pieces)


def _routine_kick_return(row):
    text=f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    if "kickoff" not in text or "return" not in text or "touchdown" in text:return False
    try:yards=int(row.get("yards_gained") or 0)
    except (TypeError,ValueError):yards=0
    return yards<45


def _render(repository,game):
    game_id=int(game.get("game_id") or 0)
    try:pace=game_pace_summary(repository,game_id)
    except Exception:pace={"teams":{}}
    try:turns=game_turning_points(repository,game_id,model_version=WP_MODEL_VERSION,limit=12)
    except Exception:turns=[]
    turns=[r for r in turns if not _routine_kick_return(r)][:6]; teams=pace.get("teams") or {}
    if not teams and not turns:return Markup("")
    colors=_team_colors(repository,game); away_team=str(game.get("away_team") or "Away"); home_team=str(game.get("home_team") or "Home"); away_color=colors.get(away_team,"var(--team-light)"); home_color=colors.get(home_team,"var(--team-light)"); efficiency_override='<style>'+f'.box-report .efficiency-row .efficiency-cell:nth-child(2).edge{{color:{escape(away_color,quote=True)}!important}}'+f'.box-report .efficiency-row .efficiency-cell:nth-child(3).edge{{color:{escape(home_color,quote=True)}!important}}'+'</style>'
    pace_html=""
    if teams:
        summaries=''.join(_summary_card(team,states) for team,states in teams.items()); details=''.join(_detail_team(team,states) for team,states in teams.items()); pace_html=f'<div class="pg-section-head"><h3>Pace & game state</h3><span>pace-v1 · situational detail on demand</span></div><div class="pg-summary-grid">{summaries}</div><details class="pg-details"><summary>View full pace splits</summary><div class="pg-detail-grid">{details}</div></details><p class="pg-note">Tempo uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a comparison proxy, not wall-clock seconds to snap.</p>'
    turn_rows=[]
    for rank,row in enumerate(turns,1):
        period,minute,second=_event_clock(row); leverage=float(row.get("leverage") or 0); before=row.get("home_wp_before"); after=row.get("home_wp_after"); event_label=_event_label(row); context=[]; scoreline=_scoreline(row,game)
        if scoreline:context.append(scoreline)
        dd=_down_distance(row); field=_field_position(row)
        if dd:context.append(dd)
        if field:context.append(field)
        offense=str(row.get("offense") or ""); defense=str(row.get("defense") or "")
        if offense:context.append(f"{offense} ball")
        if row.get("yards_gained") is not None:
            try:
                yards=int(row.get("yards_gained")); context.append(f"gain of {yards} yards" if yards>=0 else f"loss of {abs(yards)} yards")
            except (TypeError,ValueError):pass
        wp_html=""
        if before is not None and after is not None:
            direction="↑" if float(after)>float(before) else "↓" if float(after)<float(before) else "→"; wp_html=f'<div class="pg-turn-wp">{escape(home_team)} WP&nbsp; {100*float(before):.1f}% {direction} {100*float(after):.1f}%</div>'
        label_html=f'<span class="pg-turn-event">{escape(event_label)}</span>' if event_label else ""; attribution="Major event matched to surrounding valid WP states." if row.get("attribution")=="special_event" else "WP transition attributed to this pre-play state."; play_html=_play_html(str(row.get("play_text") or row.get("play_type") or "Play"),game,colors,offense,defense)
        turn_rows.append(f'<article class="pg-turn"><div class="pg-turn-rank">{rank:02d}</div><div class="pg-turn-main"><div class="pg-turn-head"><span class="pg-turn-clock">Q{period} · {minute}:{second:02d}</span>{label_html}</div><div class="pg-turn-swing"><strong>{100*leverage:.1f}</strong> win-probability points</div>{wp_html}<div class="pg-turn-context">{" · ".join(escape(piece) for piece in context)}</div></div><div class="pg-turn-detail"><p class="pg-turn-play">{play_html}</p><div class="pg-turn-attribution">{escape(attribution)}</div><div class="pg-turn-meta">WP direction is checked against {escape(EPA_MODEL_VERSION)} event-aligned play value and scoreboard/down-result sanity rules; routine kick returns and large unsupported state discontinuities are suppressed.</div></div></article>')
    turning_html=''.join(turn_rows) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'
    return Markup(STYLE+efficiency_override+'<section class="section pg-analytics">'+pace_html+f'<div class="pg-section-head"><h3>Turning points</h3><span>{escape(WP_MODEL_VERSION)} · {escape(EPA_MODEL_VERSION)}-checked event swings</span></div><div class="pg-turning">{turning_html}</div></section>')


def install_postgame_analytics_display(app):
    if app.extensions.get("postgame_analytics_display_installed"):return
    repository=app.extensions["cfb_repository"]; app.jinja_env.globals["postgame_pace_and_leverage"]=lambda game:_render(repository,dict(game)); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions["postgame_analytics_display_installed"]=True
