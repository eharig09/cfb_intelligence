"""Conservative story clustering and per-item source-role assignment."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from sports_aggregator.cfb.identity import conference_color, readable_accent
from sports_aggregator.social.content import ContentRepository


STORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
 story_id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_key TEXT NOT NULL UNIQUE,
 headline_canonical TEXT NOT NULL, story_type TEXT NOT NULL,
 first_reported_at TEXT NOT NULL, last_updated_at TEXT NOT NULL,
 confidence REAL NOT NULL, story_score REAL NOT NULL,
 primary_content_id INTEGER, clustering_method TEXT NOT NULL,
 generated_at TEXT NOT NULL,
 FOREIGN KEY(primary_content_id) REFERENCES content_items(content_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_score ON stories(story_score DESC,last_updated_at DESC);
CREATE TABLE IF NOT EXISTS story_items (
 story_id INTEGER NOT NULL, content_id INTEGER NOT NULL,
 source_role TEXT NOT NULL, role_confidence REAL NOT NULL,
 is_primary INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(story_id,content_id),
 FOREIGN KEY(story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS story_teams (
 story_id INTEGER NOT NULL, team_id INTEGER NOT NULL, PRIMARY KEY(story_id,team_id),
 FOREIGN KEY(story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS story_players (
 story_id INTEGER NOT NULL, season INTEGER NOT NULL, player_id TEXT NOT NULL,
 PRIMARY KEY(story_id,season,player_id),
 FOREIGN KEY(story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS story_games (
 story_id INTEGER NOT NULL, game_id INTEGER NOT NULL, PRIMARY KEY(story_id,game_id),
 FOREIGN KEY(story_id) REFERENCES stories(story_id) ON DELETE CASCADE
);
"""

STOPWORDS={"the","a","an","and","or","to","of","in","on","for","with","at","is","are",
           "was","be","this","that","from","as","it","its","college","football"}
TYPE_PRIORITY=("BREAKING_NEWS","INJURY","DEPTH_CHART","TRANSFER_PORTAL","RECRUITING",
               "COACHING","PLAYOFF","NFL_DRAFT","GAME_PREVIEW","GAME_RECAP","AWARDS",
               "RANKINGS","STATISTICAL_ANALYSIS","SCHEME_ANALYSIS","CONFERENCE","GOVERNANCE",
               "NIL","MEDIA","ROSTER")
CFB_TERMS=re.compile(
    r"\b(?:cfb|college football|ncaa|heisman|transfer portal|bowl game|playoff|"
    r"sec|big ten|big 12|acc|pac-12|sun belt|mountain west|conference usa|cusa|"
    r"mid-american|\bmac\b|american athletic|fbs|fcs|spring practice)\b", re.I
)
FOOTBALL_TERMS=re.compile(
    r"\b(?:quarterback|running back|wide receiver|tight end|offensive line|"
    r"defensive line|linebacker|cornerback|touchdown|depth chart|roster|kickoff|"
    r"head coach|coordinator|spring practice|season opener|redshirt)\b", re.I
)
WEAK_GENERIC_TOPICS={"GAME_PREVIEW","GAME_RECAP","MEDIA","CONFERENCE","RANKINGS",
                     "STATISTICAL_ANALYSIS","SCHEME_ANALYSIS"}
OTHER_SPORTS=re.compile(r"\b(?:soccer|basketball|baseball|softball|hockey|premier league|nba|mlb|wnba)\b",re.I)


def _is_cfb_relevant(item: dict) -> bool:
    text=f"{item['title']} {item['body_text']} {item['summary']}"
    if not (item["title"] or item["body_text"]).strip():
        return False
    if OTHER_SPORTS.search(text) and not (
        FOOTBALL_TERMS.search(text) or re.search(r"\b(?:college football|cfb)\b",text,re.I)
    ):
        return False
    if item["players"] or item["games"] or CFB_TERMS.search(text):
        return True
    if len(item["teams"]) >= 2:
        return True
    if item["teams"] and (FOOTBALL_TERMS.search(text) or item["topics"]-WEAK_GENERIC_TOPICS):
        return True
    return False


def _canonical_url(url: str) -> str:
    if not url: return ""
    parsed=urlsplit(url); host=(parsed.hostname or "").casefold().removeprefix("www.")
    if not host: return ""
    path=parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold() or "https",host,path,"",""))


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+",text.casefold())
            if len(token)>2 and token not in STOPWORDS}


def _jaccard(left: set[str],right: set[str]) -> float:
    return len(left&right)/len(left|right) if left and right else 0.0


def _headline(item: dict) -> str:
    value=(item.get("title") or item.get("body_text") or "Untitled development").strip()
    return value if len(value)<=180 else value[:177].rstrip()+"…"


def _story_type(topics: set[str]) -> str:
    return next((topic for topic in TYPE_PRIORITY if topic in topics),"DEVELOPMENT")


class StoryRepository:
    def __init__(self,database_path: str|Path) -> None:
        self.path=Path(database_path)

    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=20); connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON"); return connection

    def initialize(self):
        ContentRepository(self.path).initialize()
        with closing(self._connect()) as connection: connection.executescript(STORY_SCHEMA)

    def rebuild(self,lookback_days: int=21) -> dict:
        self.initialize(); cutoff=(datetime.now(timezone.utc)-timedelta(days=lookback_days)).isoformat()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT c.*,COALESCE(e.reliability_score,2) reliability_score
              FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
              WHERE c.published_at>=? ORDER BY c.published_at""",(cutoff,)).fetchall()
            items=[]
            for row in rows:
                item=dict(row); cid=item["content_id"]
                item["topics"]={r[0] for r in connection.execute("SELECT topic FROM content_topics WHERE content_id=?",(cid,))}
                item["teams"]={r[0] for r in connection.execute("SELECT team_id FROM content_teams WHERE content_id=?",(cid,))}
                item["players"]={(r[0],r[1]) for r in connection.execute("SELECT season,player_id FROM content_players WHERE content_id=?",(cid,))}
                item["games"]={r[0] for r in connection.execute("SELECT game_id FROM content_games WHERE content_id=? AND game_match_score>=0.75",(cid,))}
                item["tokens"]=_tokens(f"{item['title']} {item['body_text']}"); items.append(item)

            items = [item for item in items if _is_cfb_relevant(item)]

            clusters=[]; by_url=defaultdict(list); remaining=[]
            for item in items:
                url=_canonical_url(item.get("original_url") or "")
                (by_url[url] if url else remaining).append(item)
            for url,group in by_url.items(): clusters.append((f"url:{url}","EXACT_EXTERNAL_URL",group))
            for item in remaining:
                match=None
                for index,(key,method,group) in enumerate(clusters):
                    anchor=group[0]; shared_entities=bool((item["teams"]&anchor["teams"]) or (item["players"]&anchor["players"]))
                    shared_topics=bool(item["topics"]&anchor["topics"])
                    hours=abs((datetime.fromisoformat(item["published_at"].replace("Z","+00:00"))-
                               datetime.fromisoformat(anchor["published_at"].replace("Z","+00:00"))).total_seconds())/3600
                    if shared_entities and shared_topics and hours<=72 and _jaccard(item["tokens"],anchor["tokens"])>=0.55:
                        match=index; break
                if match is None:
                    digest=hashlib.sha256(f"{item['platform']}:{item['platform_content_id']}".encode()).hexdigest()[:24]
                    clusters.append((f"item:{digest}","SINGLE_ITEM",[item]))
                else: clusters[match][2].append(item)

            connection.execute("DELETE FROM story_items"); connection.execute("DELETE FROM story_teams")
            connection.execute("DELETE FROM story_players"); connection.execute("DELETE FROM story_games")
            connection.execute("DELETE FROM stories"); now=datetime.now(timezone.utc).isoformat()
            multi=0
            for key,method,group in clusters:
                if len(group)>1: multi+=1
                group.sort(key=lambda item:item["published_at"])
                topics=set().union(*(item["topics"] for item in group)); candidates=[item for item in group
                    if item["source_role"]=="REPORTING_UNDETERMINED" and item["reliability_score"]>=4]
                official=[item for item in group if item["source_role"]=="OFFICIAL_CONFIRMATION"]
                primary=(candidates or official or group)[0]; confidence=(0.9 if method=="EXACT_EXTERNAL_URL" and len(group)>1
                           else 0.75 if len(group)>1 else 0.55)
                age_hours=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(group[-1]["published_at"].replace("Z","+00:00"))).total_seconds()/3600)
                novelty=1/(1+age_hours/72); reliability=max(item["reliability_score"] for item in group)/5
                score=round(100*(0.5*reliability+0.3*novelty+0.2*min(len(group),3)/3),1)
                connection.execute("INSERT INTO stories VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                    (key,_headline(primary),_story_type(topics),group[0]["published_at"],group[-1]["published_at"],
                     confidence,score,primary["content_id"],method,now))
                story_id=connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                earliest_candidate=candidates[0]["content_id"] if candidates else None
                for item in group:
                    if item["source_role"]=="OFFICIAL_CONFIRMATION": role="OFFICIAL_CONFIRMATION"; role_conf=.95
                    elif item["source_role"]=="ANALYSIS": role="ANALYSIS"; role_conf=.9
                    elif item["source_role"]=="COMMUNITY_REACTION": role="COMMUNITY_REACTION"; role_conf=.9
                    elif item["source_role"]=="AGGREGATION": role="AGGREGATION"; role_conf=.9
                    elif item["content_id"]==earliest_candidate: role="ORIGINAL_REPORT_CANDIDATE"; role_conf=.7
                    elif item["source_role"]=="REPORTING_UNDETERMINED": role="CORROBORATION_CANDIDATE"; role_conf=.6
                    else: role=item["source_role"]; role_conf=.5
                    connection.execute("INSERT INTO story_items VALUES(?,?,?,?,?)",
                        (story_id,item["content_id"],role,role_conf,int(item["content_id"]==primary["content_id"])))
                for team_id in set().union(*(item["teams"] for item in group)):
                    connection.execute("INSERT INTO story_teams VALUES(?,?)",(story_id,team_id))
                for season,player_id in set().union(*(item["players"] for item in group)):
                    connection.execute("INSERT INTO story_players VALUES(?,?,?)",(story_id,season,player_id))
                for game_id in set().union(*(item["games"] for item in group)):
                    connection.execute("INSERT INTO story_games VALUES(?,?)",(story_id,game_id))
            connection.commit()
        return {"items":len(items),"stories":len(clusters),"multi_item_stories":multi}

    def list_stories(self,*,limit:int=30,conference:str|None=None,team_id:int|None=None,
                     game_id:int|None=None,player_id:str|None=None,
                     player_season:int|None=None) -> list[dict]:
        self.initialize(); joins=[]; conditions=[]; params=[]
        if conference:
            joins.extend(["JOIN story_teams st USING(story_id)","JOIN teams t ON t.team_id=st.team_id"])
            conditions.append("t.conference=?"); params.append(conference)
        if team_id is not None:
            joins.append("JOIN story_teams st_team USING(story_id)"); conditions.append("st_team.team_id=?"); params.append(team_id)
        if game_id is not None:
            joins.append("JOIN story_games sg USING(story_id)"); conditions.append("sg.game_id=?"); params.append(game_id)
        if player_id is not None:
            joins.append("JOIN story_players sp USING(story_id)")
            conditions.append("sp.player_id=?"); params.append(player_id)
            if player_season is not None:
                conditions.append("sp.season=?"); params.append(player_season)
        sql="SELECT DISTINCT s.* FROM stories s "+" ".join(joins)
        if conditions: sql+=" WHERE "+" AND ".join(conditions)
        sql+=" ORDER BY story_score DESC,last_updated_at DESC LIMIT ?"; params.append(limit)
        with closing(self._connect()) as connection:
            rows=connection.execute(sql,params).fetchall(); result=[]
            for row in rows:
                item=dict(row); sid=item["story_id"]
                item["title"] = item["headline_canonical"]
                item["cluster_basis"] = item["clustering_method"]
                item["sources"]=[dict(r) for r in connection.execute("""SELECT si.source_role,si.role_confidence,
                  si.is_primary,c.canonical_url,c.original_url,c.body_text,c.title,c.published_at,e.name source_name
                  FROM story_items si JOIN content_items c USING(content_id)
                  LEFT JOIN source_entities e USING(source_entity_id) WHERE si.story_id=?
                  ORDER BY si.is_primary DESC,c.published_at""",(sid,))]
                primary = item["sources"][0] if item["sources"] else {}
                item["url"] = primary.get("original_url") or primary.get("canonical_url")
                item["source_name"] = primary.get("source_name") or "Source"
                item["teams"]=[dict(r) for r in connection.execute(
                  """SELECT t.team_id,t.school,t.conference,t.color,t.logos_json
                     FROM story_teams st JOIN teams t USING(team_id) WHERE story_id=?""",(sid,))]
                for team in item["teams"]:
                    logos=json.loads(team.pop("logos_json") or "[]")
                    team["logo"]=logos[0] if logos else None
                    team["accent"]=readable_accent(team.get("color"))
                    team["conference_color"]=conference_color(team.get("conference"))
                # The first resolved team paints the block, so a reader can scan a
                # long list by color before reading a single headline.
                primary=item["teams"][0] if item["teams"] else {}
                item["accent"]=primary.get("accent")
                item["primary_team"]=primary.get("school")
                item["primary_logo"]=primary.get("logo")
                result.append(item)
        return result
