"""Event-centric turning points reconstructed from valid wp-v2 game states.

Major events own the WP transition they create, including when the event itself is
the next valid provider state. Direction checks use event-aligned ep-v2 plus actual
score changes and late-down football logic. Stored wp-v2 predictions remain intact.
"""
from __future__ import annotations

from contextlib import closing
import re
from typing import Any

EPA_MODEL_VERSION = "ep-v2"
ADMIN_WORDS = (
    "timeout", "end of quarter", "end of 1st", "end of 2nd", "end of 3rd",
    "end of 4th", "end of half", "end of game", "end of regulation", "no play",
)


def _home_score(row: dict[str, Any]) -> tuple[int | None, int | None]:
    try:
        offense_score = int(row.get("offense_score")); defense_score = int(row.get("defense_score"))
    except (TypeError, ValueError):
        return None, None
    if str(row.get("offense") or "") == str(row.get("home_team") or ""):
        return offense_score, defense_score
    return defense_score, offense_score


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()


def _administrative(row: dict[str, Any]) -> bool:
    text = _text(row)
    play_type = str(row.get("play_type") or "").casefold()
    if "touchdown" in text or "safety" in text or "field goal" in text or "intercept" in text or "fumble" in text:
        return False
    return any(word in text for word in ADMIN_WORDS) or play_type == "timeout"


def _valid_state(row: dict[str, Any]) -> bool:
    if _administrative(row): return False
    try:
        period=int(row.get("period") or 0); down=int(row.get("down") or 0); ytg=int(row.get("yards_to_goal")); distance=row.get("distance")
    except (TypeError, ValueError): return False
    return 1 <= period <= 4 and 1 <= down <= 4 and distance is not None and 1 <= ytg <= 100 and bool(str(row.get("offense") or "").strip()) and row.get("home_win_probability") is not None


def _event_priority(row: dict[str, Any]) -> int:
    if _administrative(row): return -1
    text=_text(row); scoring=int(row.get("scoring") or 0)
    if "touchdown" in text: return 100
    if "intercept" in text and ("return" in text or scoring): return 98
    if "fumble" in text and ("return" in text or "recovered" in text or scoring): return 96
    if "safety" in text: return 94
    if "field goal" in text: return 90
    if "intercept" in text or "fumble" in text: return 88
    if scoring: return 85
    if int(row.get("down") or 0) == 4: return 25
    return 0


def _choose_event(segment: list[dict[str, Any]]) -> dict[str, Any]:
    if not segment: return {}
    best=segment[0]; best_priority=_event_priority(best)
    for row in segment[1:]:
        priority=_event_priority(row)
        if priority > best_priority or (priority == best_priority and priority >= 85):
            best=row; best_priority=priority
    return best


def _ranking_score(row: dict[str, Any]) -> float:
    leverage=abs(float(row.get("wp_change") or 0.0)); period=int(row.get("period") or 0)
    try: before=float(row.get("home_wp_before"))
    except (TypeError, ValueError): before=0.5
    stage={1:.98,2:1.0,3:1.03,4:1.08}.get(period,1.0); closeness=max(0.0,1.0-2.0*abs(before-.5)); competitive=.97+.06*closeness
    priority=int(row.get("event_priority") or 0); event=1.08 if priority>=85 else (1.03 if priority>=25 else 1.0)
    return leverage*stage*competitive*event


def _directionally_consistent(row: dict[str, Any]) -> bool:
    epa=row.get("epa")
    if epa is None: return True
    try: epa_f=float(epa); wp_change=float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError): return True
    if abs(epa_f)<.05 or abs(wp_change)<.005: return True
    offense_is_home=str(row.get("offense") or "")==str(row.get("home_team") or "")
    expected_home_sign=epa_f if offense_is_home else -epa_f
    return expected_home_sign*wp_change>0


def _actual_score_change(row: dict[str, Any]) -> tuple[int,int] | None:
    vals=(row.get("home_score"),row.get("away_score"),row.get("home_score_after"),row.get("away_score_after"))
    if any(v is None for v in vals): return None
    try: return int(vals[2])-int(vals[0]), int(vals[3])-int(vals[1])
    except (TypeError, ValueError): return None


def _score_change_consistent(row: dict[str, Any]) -> bool:
    if int(row.get("event_priority") or 0)<85: return True
    delta=_actual_score_change(row)
    if delta is None: return True
    try: wp_change=float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError): return True
    if delta[0]==delta[1]: return True
    return wp_change>0 if delta[0]>delta[1] else wp_change<0


def _late_down_result_consistent(row: dict[str, Any]) -> bool:
    if int(row.get("event_priority") or 0)>=85: return True
    try: down=int(row.get("down") or 0); distance=int(row.get("distance") or 0); gained=int(row.get("yards_gained") or 0); wp_change=float(row.get("wp_change") or 0.0)
    except (TypeError, ValueError): return True
    if down not in (3,4) or distance<=0 or abs(wp_change)<.005: return True
    offense_is_home=str(row.get("offense") or "")==str(row.get("home_team") or "")
    succeeded=gained>=distance; expected_positive=succeeded if offense_is_home else not succeeded
    return wp_change>0 if expected_positive else wp_change<0


def _special_teams_return_credible(row: dict[str, Any]) -> bool:
    if int(row.get("event_priority") or 0)>=85: return True
    text=_text(row)
    if "return" not in text or not ("kickoff" in text or "punt" in text): return True
    match=re.search(r"\breturn(?:ed)?\s+(\d+)\s+yards?\b",text)
    return bool(match and int(match.group(1))>=40)


def _credible(row: dict[str, Any]) -> bool:
    if not _score_change_consistent(row): return False
    priority=int(row.get("event_priority") or 0); score_delta=_actual_score_change(row)
    if priority>=85 and score_delta is not None and score_delta!=(0,0): return True
    if not _late_down_result_consistent(row) or not _directionally_consistent(row) or not _special_teams_return_credible(row): return False
    if priority>=25: return True
    leverage=abs(float(row.get("wp_change") or 0.0))
    if leverage<=.12: return True
    try:
        period=int(row.get("period") or 0); remaining=int(row.get("clock_minutes") or 0)*60+int(row.get("clock_seconds") or 0); down=int(row.get("down") or 0); ytg=int(row.get("yards_to_goal") or 100); margin=abs(int(row.get("home_score") or 0)-int(row.get("away_score") or 0))
    except (TypeError, ValueError): return False
    if period<=3: return False
    if remaining<=300 and margin<=8 and (down>=3 or ytg<=10): return True
    return leverage<=.18


def game_turning_points(repository, game_id: int, *, model_version: str="wp-v2", limit: int=6) -> list[dict[str, Any]]:
    from sports_aggregator.cfb.win_probability import initialize
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_ep
    initialize(repository); initialize_ep(repository); game_id=int(game_id); limit=max(1,int(limit))
    with repository._reader() as connection:
        game=connection.execute("SELECT completed,home_points,away_points FROM games WHERE game_id=?",(game_id,)).fetchone()
        rows=[dict(r) for r in connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,p.down,p.distance,p.yardline,p.yards_to_goal,p.yards_gained,p.scoring,p.drive_number,p.play_number,m.rush_pass,m.down_type,w.home_win_probability,e.epa
          FROM cfb_plays p JOIN cfb_play_win_probability w USING(play_id)
          LEFT JOIN cfb_play_metrics m ON m.play_id=p.play_id AND m.metric_version='pbp-v1'
          LEFT JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=?
          WHERE p.game_id=? AND w.model_version=? AND p.period BETWEEN 1 AND 4
          ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,COALESCE(p.drive_number,0),COALESCE(p.play_number,0),p.play_id
        """,(EPA_MODEL_VERSION,game_id,model_version)).fetchall()]
    if not rows: return []
    terminal=None
    if game and int(game["completed"] or 0) and game["home_points"] is not None and game["away_points"] is not None:
        terminal=1.0 if float(game["home_points"])>float(game["away_points"]) else 0.0
    state_indices=[i for i,r in enumerate(rows) if _valid_state(r)]
    if not state_indices: return []
    candidates=[]
    for position,start_index in enumerate(state_indices):
        state=rows[start_index]; next_index=state_indices[position+1] if position+1<len(state_indices) else None; before=state.get("home_win_probability")
        if next_index is not None:
            next_state=rows[next_index]; after=next_state.get("home_win_probability")
            segment=rows[start_index:next_index]
            if _event_priority(next_state)>=85: segment=segment+[next_state]
        else:
            next_state=None
            if terminal is None: continue
            after=terminal; segment=rows[start_index:]
        if before is None or after is None: continue
        try: before_f=float(before); after_f=float(after)
        except (TypeError, ValueError): continue
        event=dict(_choose_event(segment) or state)
        for key in ("period","clock_minutes","clock_seconds","offense","defense","home_team","away_team","offense_score","defense_score","down","distance","yardline","yards_to_goal","drive_number","play_number"):
            event[f"event_{key}"]=event.get(key); event[key]=state.get(key)
        event["home_wp_before"]=before_f; event["home_wp_after"]=after_f; event["wp_change"]=after_f-before_f; event["leverage"]=abs(event["wp_change"]); event["wp_swing_points"]=100*event["leverage"]
        event["event_priority"]=_event_priority(event); event["attribution"]="special_event" if event.get("play_id")!=state.get("play_id") else "state_play"
        hs,as_=_home_score(state); event["home_score"]=hs; event["away_score"]=as_
        if next_state is not None:
            h2,a2=_home_score(next_state); event["home_score_after"]=h2; event["away_score_after"]=a2
        else:
            event["home_score_after"]=game["home_points"] if game else None; event["away_score_after"]=game["away_points"] if game else None
        event["turning_point_score"]=_ranking_score(event)
        if _credible(event): candidates.append(event)
    by_event={}
    for row in candidates:
        key=str(row.get("play_id") or ""); old=by_event.get(key)
        if old is None or float(row.get("leverage") or 0)>float(old.get("leverage") or 0): by_event[key]=row
    return sorted(by_event.values(),key=lambda r:(float(r.get("turning_point_score") or 0),float(r.get("leverage") or 0)),reverse=True)[:limit]


def scoring_event_diagnostics(repository, game_id: int, *, wp_model_version: str="wp-v2", ep_model_version: str=EPA_MODEL_VERSION) -> list[dict[str, Any]]:
    from sports_aggregator.cfb.win_probability import initialize
    from sports_aggregator.cfb.expected_points_v2 import initialize as initialize_ep
    initialize(repository); initialize_ep(repository)
    with closing(repository._connect()) as connection:
        rows=connection.execute("""SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.offense,p.defense,p.home_team,p.away_team,p.play_type,p.play_text,p.offense_score,p.defense_score,p.down,p.distance,p.yards_to_goal,p.yards_gained,p.scoring,e.epa,e.immediate_net_points,e.possession_changed,w.home_win_probability FROM cfb_plays p LEFT JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=? LEFT JOIN cfb_play_win_probability w ON w.play_id=p.play_id AND w.model_version=? WHERE p.game_id=? AND p.period BETWEEN 1 AND 4 AND (COALESCE(p.scoring,0)=1 OR LOWER(COALESCE(p.play_text,'')) LIKE '%touchdown%' OR LOWER(COALESCE(p.play_text,'')) LIKE '%intercept%' OR LOWER(COALESCE(p.play_text,'')) LIKE '%fumble%' OR LOWER(COALESCE(p.play_text,'')) LIKE '%safety%') ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,COALESCE(p.drive_number,0),COALESCE(p.play_number,0),p.play_id""",(ep_model_version,wp_model_version,int(game_id))).fetchall()
    return [dict(r) for r in rows]
