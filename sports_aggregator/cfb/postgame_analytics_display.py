"""Supplement the postgame report with compact pace/game-state and WP turning points."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from flask import url_for
from jinja2 import BaseLoader, TemplateNotFound
from markupsafe import Markup

from sports_aggregator.cfb.identity import team_identity
from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.wp_turning_points import EPA_MODEL_VERSION, game_turning_points

ANCHOR = "{{ postgame_analysis(game, team_stats, player_stats) }}"
REPLACEMENT = ANCHOR + "\n{{ postgame_pace_and_leverage(game) }}"
WP_MODEL_VERSION = "wp-v2"

STYLE = ""  # served from static/cfb_analysis.css, not inlined per render


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


#: A turning point carries two states. `game_turning_points` attributes the win
#: probability transition to the pre-play state, then overwrites the event's own
#: down/distance/field position with that state's and keeps the originals under
#: `event_*`. The transition is the state's, but the sentence a reader is looking
#: at is the event's, and mixing them produced lines that contradicted the play:
#: "4th & 7 at the TCU 27 ... gain of 9 yards" beside "rush left for 9 yards to
#: the TCU goal line TOUCHDOWN". It was 1st & goal from the 9.
def _event_field(row, key):
    value = row.get(f"event_{key}")
    return row.get(key) if value is None else value


def _down_distance(row):
    try: down=int(_event_field(row,"down")); distance=int(_event_field(row,"distance"))
    except (TypeError,ValueError): return ""
    return f"{down}{ {1:'st',2:'nd',3:'rd'}.get(down,'th') } & {distance}"


def _field_position(row):
    offense=str(_event_field(row,"offense") or ""); defense=str(_event_field(row,"defense") or "")
    try: ytg=int(_event_field(row,"yards_to_goal"))
    except (TypeError,ValueError): return ""
    return f"at the {defense} {ytg}" if ytg<=50 and defense else (f"at the {offense} {100-ytg}" if offense else "")


#: Yards gained is a scrimmage number. The provider reports a made field goal as
#: a 28-yard gain, which reads as an advance the offense never made.
_KICKING=("field goal","punt","kickoff","extra point","pat")


def _is_scrimmage(row):
    text=f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    return not any(cue in text for cue in _KICKING)


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


def _roster_index(repository,game):
    """Every way the two rosters are written in play text, mapped to a player.

    Resolving against the roster rather than against the shape of a name is what
    makes the names in a turning point linkable, and it fixes the matching too:
    the pattern below only ever recognised "#12 Milroe" and "J. Milroe", while
    the provider writes "Jalen Milroe" most of the time and "Milroe,Jalen" on the
    rows that carry formation. Knowing the player also settles which team to
    colour them, which was previously guessed from the surrounding words.
    """
    season=int(game.get("season") or 0); index={}; ambiguous=set()
    if not season:return index
    for side in ("away","home"):
        team=str(game.get(f"{side}_team") or "")
        if not team:continue
        try:
            with repository._reader() as connection:
                rows=connection.execute(
                    "SELECT player_id,first_name,last_name FROM players WHERE season=? AND team=?",
                    (season,team)).fetchall()
        except Exception:continue
        for row in rows:
            first=str(row["first_name"] or "").strip(); last=str(row["last_name"] or "").strip()
            if not last:continue
            entry=(str(row["player_id"]),team)
            variants=[f"{first} {last}",f"{last},{first}",f"{last}, {first}"] if first else []
            if first:variants+=[f"{first[0]}.{last}",f"{first[0]}. {last}"]
            for variant in variants:
                key=variant.casefold()
                if key in index and index[key]!=entry:ambiguous.add(key)
                index[key]=entry
    for key in ambiguous:index.pop(key,None)
    return index


def _play_pattern(index):
    """Roster names first, then the generic shapes, so the specific one wins.

    The case-insensitivity is scoped to the roster half. `_PLAYER` uses [A-Z] to
    mean a capital, and compiling the whole pattern with re.I broke that: it
    matched "by" in "#1 by MSH." and highlighted it as a player on 69 plays in
    60,000. The roster names still match whatever case the provider wrote.
    """
    if not index:return _PLAYER
    names="|".join(re.escape(name) for name in sorted(index,key=len,reverse=True))
    return re.compile(f"(?P<roster>(?i:{names}))|(?P<generic>{_PLAYER.pattern})")


def _play_html(text,game,colors,offense,defense,index=None):
    human=_clean_play_text(text,game); pieces=[]; last=0
    index=index or {}; pattern=_play_pattern(index)
    for match in pattern.finditer(human):
        pieces.append(escape(human[last:match.start()])); shown=match.group(0)
        resolved=index.get(shown.casefold()) if match.groupdict().get("roster") else None
        if resolved:
            player_id,team=resolved
            try:href=url_for("cfb.player_preview",player_id=player_id,season=int(game.get("season") or 0))
            except Exception:href=None
        else:
            href=None
            before=human[max(0,match.start()-70):match.start()].rstrip()
            defender=(match.start()>0 and human[match.start()-1]=="(") or bool(_DEFENSE_CONTEXT.search(before)) or any(before.casefold().endswith(cue) for cue in _DEFENSE_CUES)
            team=defense if defender else offense
        color=colors.get(team,"var(--team-light)")
        style=f'class="pg-turn-player" style="color:{escape(color,quote=True)}"'
        pieces.append(f'<a href="{escape(href,quote=True)}" {style}>{escape(shown)}</a>' if href
                      else f'<span {style}>{escape(shown)}</span>')
        last=match.end()
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
    # Every other section of this report says why it is empty. This one used to
    # return "" and take its own headings with it, so a game without scored
    # win-probability read as a section that had been deleted rather than one
    # waiting on a pipeline step.
    colors=_team_colors(repository,game); roster=_roster_index(repository,game); away_team=str(game.get("away_team") or "Away"); home_team=str(game.get("home_team") or "Home"); away_color=colors.get(away_team,"var(--team-light)"); home_color=colors.get(home_team,"var(--team-light)"); efficiency_override='<style>'+f'.box-report .efficiency-row .efficiency-cell:nth-child(2).edge{{color:{escape(away_color,quote=True)}!important}}'+f'.box-report .efficiency-row .efficiency-cell:nth-child(3).edge{{color:{escape(home_color,quote=True)}!important}}'+'</style>'
    pace_html=f'<div class="pg-section-head"><h3>Pace &amp; game state</h3><span>pace-v1</span></div><div class="empty">No scored play-by-play is stored for this game, so pace and game state cannot be computed.</div>'
    if teams:
        summaries=''.join(_summary_card(team,states) for team,states in teams.items()); details=''.join(_detail_team(team,states) for team,states in teams.items()); pace_html=f'<div class="pg-section-head"><h3>Pace & game state</h3><span>pace-v1 · situational detail on demand</span></div><div class="pg-summary-grid">{summaries}</div><details class="pg-details"><summary>View full pace splits</summary><div class="pg-detail-grid">{details}</div></details><p class="pg-note">Tempo uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a comparison proxy, not wall-clock seconds to snap.</p>'
    turn_rows=[]
    for rank,row in enumerate(turns,1):
        period,minute,second=_event_clock(row); leverage=float(row.get("leverage") or 0); before=row.get("home_wp_before"); after=row.get("home_wp_after"); event_label=_event_label(row); context=[]; scoreline=_scoreline(row,game)
        if scoreline:context.append(scoreline)
        dd=_down_distance(row); field=_field_position(row)
        if dd:context.append(dd)
        if field:context.append(field)
        offense=str(_event_field(row,"offense") or ""); defense=str(_event_field(row,"defense") or "")
        if offense:context.append(f"{offense} ball")
        if row.get("yards_gained") is not None and _is_scrimmage(row):
            try:
                yards=int(row.get("yards_gained")); context.append(f"gain of {yards} yards" if yards>=0 else f"loss of {abs(yards)} yards")
            except (TypeError,ValueError):pass
        wp_html=""
        if before is not None and after is not None:
            direction="↑" if float(after)>float(before) else "↓" if float(after)<float(before) else "→"; wp_html=f'<div class="pg-turn-wp">{escape(home_team)} WP&nbsp; {100*float(before):.1f}% {direction} {100*float(after):.1f}%</div>'
        label_html=f'<span class="pg-turn-event">{escape(event_label)}</span>' if event_label else ""; attribution="Major event matched to surrounding valid WP states." if row.get("attribution")=="special_event" else "WP transition attributed to this pre-play state."; play_html=_play_html(str(row.get("play_text") or row.get("play_type") or "Play"),game,colors,offense,defense,roster)
        turn_rows.append(f'<article class="pg-turn"><div class="pg-turn-rank">{rank:02d}</div><div class="pg-turn-main"><div class="pg-turn-head"><span class="pg-turn-clock">Q{period} · {minute}:{second:02d}</span>{label_html}</div><div class="pg-turn-swing"><strong>{100*leverage:.1f}</strong> win-probability points</div>{wp_html}<div class="pg-turn-context">{" · ".join(escape(piece) for piece in context)}</div></div><div class="pg-turn-detail"><p class="pg-turn-play">{play_html}</p><div class="pg-turn-attribution">{escape(attribution)}</div><div class="pg-turn-meta">WP direction is checked against {escape(EPA_MODEL_VERSION)} event-aligned play value and scoreboard/down-result sanity rules; routine kick returns and large unsupported state discontinuities are suppressed.</div></div></article>')
    turning_html=''.join(turn_rows) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'
    return Markup(STYLE+efficiency_override+'<section class="section pg-analytics">'+pace_html+f'<div class="pg-section-head"><h3>Turning points</h3><span>{escape(WP_MODEL_VERSION)} · {escape(EPA_MODEL_VERSION)}-checked event swings</span></div><div class="pg-turning">{turning_html}</div></section>')


def install_postgame_analytics_display(app):
    if app.extensions.get("postgame_analytics_display_installed"):return
    repository=app.extensions["cfb_repository"]; app.jinja_env.globals["postgame_pace_and_leverage"]=lambda game:_render(repository,dict(game)); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions["postgame_analytics_display_installed"]=True
