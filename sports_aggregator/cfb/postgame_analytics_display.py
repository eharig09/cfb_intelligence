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


def _team_abbrevs(repository,game):
    """Provider field codes -> team name, from the stored `teams.abbreviation`.

    `_team_codes` only ever *guessed* an abbreviation from the school name, so
    "Eastern Michigan" produced EM / EAS / EAST and never EMU, and the raw
    "EMU16" leaked into the sentence unchanged. The stored abbreviation is the
    code the play text actually uses.
    """
    out={}
    for side in ("away","home"):
        team=str(game.get(f"{side}_team") or ""); team_id=game.get(f"{side}_team_id")
        if not team or team_id is None:continue
        try:
            with repository._reader() as connection:
                row=connection.execute("SELECT abbreviation FROM teams WHERE team_id=?",(int(team_id),)).fetchone()
        except Exception:row=None
        code=str((row["abbreviation"] if row else "") or "").strip().upper()
        if len(code)>=2:out[code]=team
    return out


def _humanize_field_codes(text,game,abbrevs=None):
    code_map=dict(abbrevs or {})
    for team in (str(game.get("away_team") or "Away"),str(game.get("home_team") or "Home")):
        for code in _team_codes(team):code_map.setdefault(code,team)
    def repl(m):
        team=code_map.get(m.group(1).upper()); yard=int(m.group(2))
        return m.group(0) if not team else (f"{team} goal line" if yard==0 else f"{team} {yard}")
    return re.sub(r"\b([A-Z]{2,6})(\d{2})\b",repl,text)


def _clean_play_text(text,game,abbrevs=None):
    human=_humanize_field_codes(text,game,abbrevs); human=re.sub(r"^\(\d{1,2}:\d{2}\)\s*","",human).strip(); human=re.sub(r",?\s*clock\s+\d{1,2}:\d{2}","",human,flags=re.I)
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


def _resolve_player(shown,index,match):
    """A player token -> (player_id, team), tolerant of the jersey prefix.

    The roster index is keyed on name forms ("l.weaver", "landon weaver"); the
    play text writes "#3 L.Weaver". Without stripping the "#3 " the lookup
    always missed, so every jersey-prefixed name fell through to a positional
    guess -- and the guess disagreed with itself when the same player was named
    twice in one play (the fumble, then its return).
    """
    key=shown.casefold()
    if match.groupdict().get("roster"):
        return index.get(key)
    bare=re.sub(r"^#\d+\s+","",key)
    return index.get(bare) or index.get(bare.replace(" ",""))


def _play_html(text,game,colors,offense,defense,index=None,abbrevs=None):
    human=_clean_play_text(text,game,abbrevs); index=index or {}; pattern=_play_pattern(index)
    season=int(game.get("season") or 0)
    # First pass: decide each distinct token once so both mentions of a player
    # in one play get the same colour and the same link.
    decided={}
    for match in pattern.finditer(human):
        key=match.group(0).casefold()
        if key in decided:continue
        resolved=_resolve_player(match.group(0),index,match)
        if resolved:
            player_id,team=resolved
            try:href=url_for("cfb.player_preview",player_id=player_id,season=season)
            except Exception:href=None
        else:
            href=None; before=human[max(0,match.start()-70):match.start()].rstrip()
            defender=(match.start()>0 and human[match.start()-1]=="(") or bool(_DEFENSE_CONTEXT.search(before)) or any(before.casefold().endswith(cue) for cue in _DEFENSE_CUES)
            team=defense if defender else offense
        decided[key]=(team,href)
    pieces=[]; last=0
    for match in pattern.finditer(human):
        pieces.append(escape(human[last:match.start()])); shown=match.group(0)
        team,href=decided.get(shown.casefold(),(offense,None))
        color=colors.get(team,"var(--team-light)")
        style=f'class="pg-turn-player" style="color:{escape(color,quote=True)}"'
        pieces.append(f'<a href="{escape(href,quote=True)}" {style}>{escape(shown)}</a>' if href
                      else f'<span {style}>{escape(shown)}</span>')
        last=match.end()
    pieces.append(escape(human[last:])); return "".join(pieces)


def _wp_meter_html(row,home_team,away_team):
    """The headline: home win probability before -> after, as one bar.

    The bar is the swing; the two dots are the two states. It is tinted in the
    colour of the team the swing helped, so "who this moved" reads at a glance
    instead of being decoded from an up/down arrow on a percentage.
    """
    b,a=row.get("home_wp_before"),row.get("home_wp_after")
    if b is None or a is None:return ""
    try:b=float(b); a=float(a)
    except (TypeError,ValueError):return ""
    arrow="▲" if a>b else ("▼" if a<b else "▶")
    lo=min(a,b); span=abs(a-b)
    # The swing size and the team it helped are in the card's lead sentence, so
    # this is just the two states and the bar. The label names whose WP it is.
    return (f'<div class="pg-turn-wp">'
            f'<div class="pg-turn-wp-track" style="--lo:{100*lo:.1f}%;--span:{100*span:.1f}%">'
            f'<i class="pg-turn-wp-fill"></i>'
            f'<i class="pg-turn-wp-dot before" style="left:{100*b:.1f}%"></i>'
            f'<i class="pg-turn-wp-dot after" style="left:{100*a:.1f}%"></i></div>'
            f'<div class="pg-turn-wp-read"><span class="pg-turn-wp-name">{escape(home_team)} WP</span>'
            f'<b>{100*b:.0f}%</b> {arrow} <b>{100*a:.0f}%</b></div></div>')


def _field_read(row,label):
    # Just the spot. The down & distance is in the strip's label; the yards and
    # the outcome are in the card's lead sentence. This says where on the field.
    field=_field_position(row)
    return escape(field) if field else ""


def _field_svg(row):
    """A compact drive strip drawn from yards_to_goal and yards_gained.

    Structured numbers only -- it does not read team codes out of the play text,
    which is the part that was wrong from one game to the next. Offense attacks
    left -> right toward the defending end zone on the right.
    """
    try:ytg=max(1,min(100,int(_event_field(row,"yards_to_goal"))))
    except (TypeError,ValueError):return ""
    label=_event_label(row)
    td="Touchdown" in label or label in ("Pick-six","Fumble TD")
    turnover=label in ("Interception","Fumble","Pick-six","Fumble TD")
    fg=label=="Field goal"
    try:gained=int(row.get("yards_gained") or 0)
    except (TypeError,ValueError):gained=0
    L,R=10.0,110.0; los=L+(100-ytg)
    parts=['<svg class="pg-turn-field-svg" viewBox="0 0 120 18" preserveAspectRatio="none" aria-hidden="true">',
           '<rect class="pg-f-turf" x="10" y="2" width="100" height="14"/>',
           '<rect class="pg-f-ez" x="0" y="2" width="10" height="14"/>',
           f'<rect class="pg-f-ez pg-f-target{" scored" if td and not turnover else ""}" x="110" y="2" width="10" height="14"/>']
    parts+= [f'<line class="pg-f-yd" x1="{x}" y1="2" x2="{x}" y2="16"/>' for x in range(20,110,10)]
    parts.append(f'<line class="pg-f-los" x1="{los:.1f}" y1="0" x2="{los:.1f}" y2="18"/>')
    if fg:
        parts.append(f'<rect class="pg-f-fg" x="{los-1.3:.1f}" y="4" width="2.6" height="10"/>')
    elif turnover:
        parts.append(f'<rect class="pg-f-turnover" x="{los-1.6:.1f}" y="0" width="3.2" height="18"/>')
        if td:parts.append('<rect class="pg-f-drive pg-f-defense" x="1.5" y="5" width="8.5" height="8"/>')
    else:
        end=max(L,min(R,los+gained)); x0,w=min(los,end),abs(end-los)
        parts.append(f'<rect class="pg-f-drive{" td" if td else ""}" x="{x0:.1f}" y="5" width="{max(w,1.0):.1f}" height="8"/>')
    parts.append('</svg>')
    return "".join(parts)


def _routine_kick_return(row):
    text=f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
    if "kickoff" not in text or "return" not in text or "touchdown" in text:return False
    try:yards=int(row.get("yards_gained") or 0)
    except (TypeError,ValueError):yards=0
    return yards<45


def _score_chip(row,game,abbrevs):
    scoring=int(row.get("event_priority") or 0)>=85
    hs=row.get("home_score_after") if scoring else row.get("home_score")
    as_=row.get("away_score_after") if scoring else row.get("away_score")
    if hs is None or as_ is None:return ""
    def short(side):
        team=str(game.get(f"{side}_team") or "")
        for code,name in (abbrevs or {}).items():
            if name==team:return code
        return team[:4].upper() if team else side[:4].upper()
    return f'<span class="pg-turn-score">{short("away")} {int(as_)} &middot; {short("home")} {int(hs)}</span>'


def _key_player(text,roster):
    """The first named player in the cleaned play text, jersey prefix dropped."""
    for match in _play_pattern(roster or {}).finditer(text):
        token=match.group(0)
        if match.groupdict().get("roster") or re.match(r"^#\d+\s+[A-Z]",token):
            return re.sub(r"^#\d+\s+","",token).strip()
    return ""


def _turn_summary(row,game,roster,home_team,away_team):
    """One plain sentence: what happened, then what it did to the game.

    The card has every fact -- the event chip, the two WP numbers, the field
    strip, the verbatim play -- but a reader had to assemble the story from all
    of them. This states it once.
    """
    label=_event_label(row)
    offense=str(_event_field(row,"offense") or "the offense")
    defense=str(_event_field(row,"defense") or "the defense")
    raw=str(row.get("play_text") or ""); low=raw.casefold()
    key=_key_player(_clean_play_text(raw,game),roster)
    try:down=int(_event_field(row,"down"))
    except (TypeError,ValueError):down=0
    try:distance=int(_event_field(row,"distance"))
    except (TypeError,ValueError):distance=0
    try:gained=int(row.get("yards_gained")); yards=abs(gained)
    except (TypeError,ValueError):gained,yards=0,0
    is_pass=("pass" in low) or ("sack" in low) or (str(row.get("play_type") or "").casefold()=="pass")

    if label in ("Pick-six","Fumble TD"):
        kind="interception" if "Pick" in label else "fumble"
        what=f"{escape(defense)} returned {'an' if kind[0] in 'aeiou' else 'a'} {kind} for a touchdown"
        if key:what+=f" &mdash; {escape(key)}"
    elif "Touchdown" in label:
        # In a scoring summary CFBD names the scorer first, so `key` is who
        # reached the end zone: "{team} scored -- Name Nn-yard catch/run".
        act="catch" if is_pass else "run"
        what=f"{escape(offense)} scored"
        if key and yards:what+=f" &mdash; {escape(key)} {yards}-yard {act}"
        elif key:what+=f" &mdash; {escape(key)}"
        elif yards:what+=f" on a {yards}-yard {'pass' if is_pass else 'run'}"
    elif label=="Field goal":
        what=f"{escape(offense)} made a field goal" + (f" &mdash; {escape(key)}" if key else "")
    elif label in ("Interception","Fumble"):
        what=f"{escape(offense)} lost the ball on {'an interception' if label=='Interception' else 'a fumble'}"
        if key:what+=f" &mdash; {escape(key)}"
    elif down==4 and distance>0:
        what=(f"{escape(offense)} converted 4th &amp; {distance}" if gained>=distance
              else f"{escape(offense)} came up short on 4th &amp; {distance}")
    elif yards:
        what=f"{escape(offense)} {'gained' if gained>=0 else 'lost'} {yards} yards"
    else:
        what=f"{escape(offense)} kept a drive alive"

    changed=""
    b,a=row.get("home_wp_before"),row.get("home_wp_after")
    try:
        b=float(b); a=float(a); helped=home_team if a>b else (away_team if a<b else "")
        swing=round(100*abs(a-b)); ha=100*a
        if helped and (ha>=99 or ha<=1):changed=f"and sealed it for {escape(helped)}"
        elif helped and swing>=1:changed=f"a {swing}-point swing to {escape(helped)}"
    except (TypeError,ValueError):pass
    sentence=f"{what}, {changed}." if changed else f"{what}."
    return f'<p class="pg-turn-summary">{sentence}</p>'


def _turn_card(rank,row,game,colors,roster,abbrevs,home_team,away_team):
    period,minute,second=_event_clock(row); label=_event_label(row)
    offense=str(_event_field(row,"offense") or ""); defense=str(_event_field(row,"defense") or "")
    off_color=colors.get(offense,"var(--team-light)")
    a=row.get("home_wp_after"); b=row.get("home_wp_before")
    try:helped=home_team if float(a)>float(b) else away_team
    except (TypeError,ValueError):helped=home_team
    help_color=colors.get(helped,"var(--team-light)")
    label_html=f'<span class="pg-turn-event">{escape(label)}</span>' if label else ""
    fl=[p for p in (_down_distance(row),f"{offense} ball" if offense else "") if p]
    field_label=" &middot; ".join(escape(p) for p in fl) or "Turning point"
    play_html=_play_html(str(row.get("play_text") or row.get("play_type") or "Play"),game,colors,offense,defense,roster,abbrevs)
    return (f'<article class="pg-turn" style="--turn-off:{escape(str(off_color),quote=True)};--turn-help:{escape(str(help_color),quote=True)}">'
            f'<div class="pg-turn-rank">{rank:02d}</div>'
            f'<div class="pg-turn-body">'
            f'<div class="pg-turn-head"><span class="pg-turn-clock">Q{period} &middot; {minute}:{second:02d}</span>{label_html}{_score_chip(row,game,abbrevs)}</div>'
            f'{_turn_summary(row,game,roster,home_team,away_team)}'
            f'<div class="pg-turn-viz">{_wp_meter_html(row,home_team,away_team)}'
            f'<div class="pg-turn-field"><div class="pg-turn-field-label">{field_label}</div>{_field_svg(row)}'
            f'<div class="pg-turn-field-read">{_field_read(row,label)}</div></div></div>'
            f'<details class="pg-turn-play"><summary>Play call</summary><p>{play_html}</p></details></div></article>')


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
    colors=_team_colors(repository,game); roster=_roster_index(repository,game); abbrevs=_team_abbrevs(repository,game); away_team=str(game.get("away_team") or "Away"); home_team=str(game.get("home_team") or "Home"); away_color=colors.get(away_team,"var(--team-light)"); home_color=colors.get(home_team,"var(--team-light)"); efficiency_override='<style>'+f'.box-report .efficiency-row .efficiency-cell:nth-child(2).edge{{color:{escape(away_color,quote=True)}!important}}'+f'.box-report .efficiency-row .efficiency-cell:nth-child(3).edge{{color:{escape(home_color,quote=True)}!important}}'+'</style>'
    pace_html='<div class="pg-section-head"><h3>Pace &amp; game state</h3><span>Snap tempo and pass rate by game state</span></div><div class="empty">No scored play-by-play is stored for this game, so pace and game state cannot be computed.</div>'
    if teams:
        summaries=''.join(_summary_card(team,states) for team,states in teams.items()); details=''.join(_detail_team(team,states) for team,states in teams.items()); pace_html=f'<div class="pg-section-head"><h3>Pace & game state</h3><span>Snap tempo and pass rate by game state</span></div><div class="pg-summary-grid">{summaries}</div><details class="pg-details"><summary>View full pace splits</summary><div class="pg-detail-grid">{details}</div></details><p class="pg-note">Tempo uses represented same-drive game-clock intervals between qualifying rush/pass snaps; it is a comparison proxy, not wall-clock seconds to snap.</p>'
    turn_rows=[_turn_card(rank,row,game,colors,roster,abbrevs,home_team,away_team) for rank,row in enumerate(turns,1)]
    if turn_rows:
        turn_rows.append('<p class="pg-turn-foot">Ranked by the size of the win-probability swing. '
                         f'Direction is checked against the {escape(EPA_MODEL_VERSION)} play value and the '
                         'scoreboard; routine kick returns and unsupported state jumps are dropped. The bar '
                         'is home win probability before &rarr; after, tinted for the team the swing helped; '
                         'the strip is the pre-play spot and the yards the play gained.</p>')
    turning_html=''.join(turn_rows) or f'<div class="empty">Fit and score {escape(WP_MODEL_VERSION)} to identify leverage and turning points.</div>'
    return Markup(STYLE+efficiency_override+'<section class="section pg-analytics" id="leverage">'+pace_html+'<div class="pg-section-head"><h3>Turning points</h3><span>Where the win probability moved most</span></div>'+f'<div class="pg-turning">{turning_html}</div></section>')


def install_postgame_analytics_display(app):
    if app.extensions.get("postgame_analytics_display_installed"):return
    repository=app.extensions["cfb_repository"]; app.jinja_env.globals["postgame_pace_and_leverage"]=lambda game:_render(repository,dict(game)); app.jinja_loader=_Loader(app.jinja_loader); app.jinja_env.cache.clear(); app.extensions["postgame_analytics_display_installed"]=True
