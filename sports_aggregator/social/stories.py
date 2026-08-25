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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sports_aggregator.cfb.identity import (
    conference_color, conference_color_dark, dark_accent, readable_accent)
from sports_aggregator.cfb.repository import (
    _logo_pair, _mark_schema_current, _schema_is_current)
from sports_aggregator.social.content import ContentRepository, label_linked_piece


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
               "NIL","MEDIA","ROSTER","DISCIPLINE","BOWL","SCHEDULE","OFFSEASON",
               "SEASON_PREVIEW","BETTING","COMMENTARY","FACILITIES")
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


#: Query parameters that identify *which* resource a URL points at. Stripping the
#: whole query string collapsed every YouTube video onto "youtube.com/watch",
#: which then clustered 87 unrelated videos into a single story.
#: Tracking parameters that never identify a resource and should not split one.
TRACKING_PREFIXES = ("utm_", "fb", "gcl", "ig_", "mc_")
TRACKING_PARAMS = {"s", "t", "ref", "source", "cmp", "campaign", "sh", "si",
                   "feature", "app", "spm", "at_medium", "at_campaign"}

#: Hosts whose links are the platform's own permalink rather than an article.
#: A self-link says "this is where the post lives", not "this is the story".
PLATFORM_HOSTS = {
    "bluesky": ("bsky.app",),
    "reddit": ("reddit.com", "redd.it"),
    "youtube": ("youtube.com", "youtu.be"),
    "podcast": (),
}


def _canonical_url(url: str) -> str:
    """Normalize a URL for comparison, preserving what identifies the resource.

    Tracking parameters are dropped so the same article shared twice matches;
    identifying parameters are kept so two different resources never collide.
    """
    if not url:
        return ""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not host:
        return ""
    path = parsed.path.rstrip("/") or "/"
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES):
            continue
        # Identity-bearing query names are publisher-specific, so retain every
        # parameter that is not known tracking noise. This is essential for
        # YouTube's ``v`` value, but also prevents the same collision on less
        # common CMS query schemes.
        kept.append((lowered, value))
    query = urlencode(sorted(kept))
    scheme = "https" if parsed.scheme.casefold() in {"http", "https", ""} else parsed.scheme.casefold()
    return urlunsplit((scheme, host, path, query, ""))


def _external_article_url(item: dict) -> str:
    """The outside article an item points at, if it points at one at all.

    A platform permalink is not a story key. Two Bluesky posts both linking to
    their own bsky.app URLs are not the same story, and clustering on that put
    unrelated items together while leaving genuinely duplicated coverage apart.
    """
    canonical = _canonical_url(item.get("original_url") or "")
    if not canonical:
        return ""
    host = urlsplit(canonical).hostname or ""
    for platform_host in PLATFORM_HOSTS.get(item.get("platform") or "", ()):
        if host == platform_host or host.endswith("." + platform_host):
            return ""
    return canonical


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


#: Roles that represent journalism, for primary-source selection.
REPORTING_ROLES = {
    "ORIGINAL_REPORT",
    "REPORTING",
    "CORROBORATION",
    # Kept for content ingested before and during the role-classifier migration.
    "REPORTING_UNDETERMINED",
}

#: How similar two items' words must be to be treated as the same story.
#: Applied together with a shared entity and a shared topic, never alone.
SIMILARITY_THRESHOLD = 0.42

#: A shared resolved game or player is much stronger evidence than a shared
#: team, so it earns a lower wording bar.
STRONG_ENTITY_THRESHOLD = 0.28

#: Items further apart than this are separate stories even if they read alike.
CLUSTER_WINDOW_HOURS = 72

#: Above this many members a cluster has almost certainly over-merged. Capping
#: keeps one bad match from swallowing a feed the way the URL bug did.
MAX_CLUSTER_SIZE = 12


def _similarity(item: dict, anchor: dict) -> tuple[bool, str] | tuple[bool, None]:
    """Whether two items are the same story, and on what evidence."""
    try:
        gap = abs((datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                   - datetime.fromisoformat(anchor["published_at"].replace("Z", "+00:00"))
                   ).total_seconds()) / 3600
    except (ValueError, KeyError):
        return False, None
    if gap > CLUSTER_WINDOW_HOURS:
        return False, None
    shared_games = item["games"] & anchor["games"]
    shared_players = item["players"] & anchor["players"]
    shared_teams = item["teams"] & anchor["teams"]
    if not (shared_games or shared_players or shared_teams):
        return False, None
    if not (item["topics"] & anchor["topics"]):
        return False, None
    overlap = _jaccard(item["tokens"], anchor["tokens"])
    if shared_games or shared_players:
        if overlap >= STRONG_ENTITY_THRESHOLD:
            return True, "SHARED_SUBJECT"
    if shared_teams and overlap >= SIMILARITY_THRESHOLD:
        return True, "SHARED_TEAM_TOPIC"
    return False, None


#: Above this many items from a single source sharing one "external" URL, the
#: link is boilerplate -- a show's homepage in every episode, a channel link in
#: every description -- rather than the article they are all about.
BOILERPLATE_URL_LIMIT = 3


def _boilerplate_urls(items: list[dict]) -> set[str]:
    """URLs that one source repeats across its own output."""
    by_url: dict[str, set] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        url = _external_article_url(item)
        if not url:
            continue
        by_url[url].add((item.get("platform"), item.get("source_entity_id")))
        counts[url] += 1
    return {url for url, count in counts.items()
            if count > BOILERPLATE_URL_LIMIT and len(by_url[url]) <= 1}


def _source_identity(item: dict) -> tuple[str | None, int | None]:
    """Stable-enough identity used to prevent a source clustering with itself."""
    return item.get("platform"), item.get("source_entity_id")


def _best_cross_source_match(
    left: list[dict], right: list[dict]
) -> tuple[float, str | None]:
    """Return the strongest supported match between two independent sources."""
    best_score = 0.0
    best_method = None
    for left_item in left:
        for right_item in right:
            if _source_identity(left_item) == _source_identity(right_item):
                continue
            matched, method = _similarity(left_item, right_item)
            if not matched:
                continue
            score = _jaccard(left_item["tokens"], right_item["tokens"])
            if score > best_score:
                best_score, best_method = score, method
    return best_score, best_method


def _build_clusters(items: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """Group content into stories.

    Exact shared-article groups are seeded first. All seeds are then considered
    for similarity merges, including seeds with different article URLs. A merge
    requires evidence from different sources, a shared confidently resolved
    subject, a shared topic, and sufficient wording overlap.
    """
    clusters: list[tuple[str, str, list[dict]]] = []
    by_article: dict[str, list[dict]] = defaultdict(list)
    loose: list[dict] = []
    boilerplate = _boilerplate_urls(items)
    for item in items:
        url = _external_article_url(item)
        if url in boilerplate:
            url = ""
        (by_article[url] if url else loose).append(item)
    for url, group in by_article.items():
        method = "SHARED_ARTICLE" if len(group) > 1 else "SINGLE_ITEM"
        clusters.append((f"url:{url}", method, group))
    for item in loose:
        digest = hashlib.sha256(
            f"{item['platform']}:{item['platform_content_id']}".encode()).hexdigest()[:24]
        clusters.append((f"item:{digest}", "SINGLE_ITEM", [item]))

    # Generate possible comparisons from shared subject/topic buckets. The old
    # rebuild repeatedly compared every cluster with every other cluster after
    # each merge, which became effectively cubic at a few thousand items. These
    # buckets are lossless with respect to ``_similarity``: a valid match must
    # share at least one team/player/game and one topic, so unrelated pairs never
    # need a wording comparison.
    candidate_buckets: dict[tuple, set[int]] = defaultdict(set)
    for cluster_index, (_, _, group) in enumerate(clusters):
        for item in group:
            for topic in item["topics"]:
                for game in item["games"]:
                    candidate_buckets[("game", game, topic)].add(cluster_index)
                for player in item["players"]:
                    candidate_buckets[("player", player, topic)].add(cluster_index)
                for team in item["teams"]:
                    candidate_buckets[("team", team, topic)].add(cluster_index)

    candidate_pairs: set[tuple[int, int]] = set()
    for indexes in candidate_buckets.values():
        ordered = sorted(indexes)
        for position, left_index in enumerate(ordered):
            candidate_pairs.update(
                (left_index, right_index) for right_index in ordered[position + 1:]
            )

    edges = []
    for left_index, right_index in candidate_pairs:
        score, method = _best_cross_source_match(
            clusters[left_index][2], clusters[right_index][2])
        if method:
            edges.append((score, left_index, right_index, method))
    edges.sort(reverse=True)

    # Kruskal-style union applies the strongest supported merge first, matching
    # the previous agglomerative intent while evaluating each possible edge only
    # once. The size cap still prevents a generic subject from swallowing a feed.
    parent = list(range(len(clusters)))
    sizes = [len(cluster[2]) for cluster in clusters]
    keys = [cluster[0] for cluster in clusters]
    methods = [cluster[1] for cluster in clusters]
    groups = [list(cluster[2]) for cluster in clusters]

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for _, left_index, right_index, method in edges:
        left_root, right_root = find(left_index), find(right_index)
        if left_root == right_root or sizes[left_root] + sizes[right_root] > MAX_CLUSTER_SIZE:
            continue
        # Keep the earlier seed as the stable story key.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        sizes[left_root] += sizes[right_root]
        groups[left_root].extend(groups[right_root])
        methods[left_root] = method

    return [(keys[index], methods[index], groups[index])
            for index in range(len(clusters)) if find(index) == index]


class StoryRepository:
    def __init__(self,database_path: str|Path) -> None:
        self.path=Path(database_path)

    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=20); connection.row_factory=sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON"); return connection

    def initialize(self):
        if _schema_is_current("stories", self.path):
            return
        ContentRepository(self.path).initialize()
        with closing(self._connect()) as connection: connection.executescript(STORY_SCHEMA)
        _mark_schema_current("stories", self.path)

    def rebuild(self,lookback_days: int=21) -> dict:
        self.initialize(); cutoff=(datetime.now(timezone.utc)-timedelta(days=lookback_days)).isoformat()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT c.*,COALESCE(e.reliability_score,2) reliability_score,
              sd.eligible cfb_eligible,sd.decision sport_decision
              FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
              LEFT JOIN content_sport_decisions sd USING(content_id)
              WHERE c.published_at>=? ORDER BY c.published_at""",(cutoff,)).fetchall()
            items=[]
            for row in rows:
                item=dict(row); cid=item["content_id"]
                item["topics"]={r[0] for r in connection.execute("SELECT topic FROM content_topics WHERE content_id=?",(cid,))}
                # A list mention is not a subject. Clustering on 0.3-confidence
                # teams let a roundup naming twenty programmes bind to anything.
                item["teams"]={r[0] for r in connection.execute(
                    "SELECT team_id FROM content_teams WHERE content_id=? AND confidence>=0.75",(cid,))}
                item["players"]={(r[0],r[1]) for r in connection.execute(
                    "SELECT season,player_id FROM content_players WHERE content_id=? AND confidence>=0.75",(cid,))}
                item["games"]={r[0] for r in connection.execute("SELECT game_id FROM content_games WHERE content_id=? AND game_match_score>=0.75",(cid,))}
                item["tokens"]=_tokens(f"{item['title']} {item['body_text']}"); items.append(item)

            # Persisted sport decisions are authoritative. The legacy fallback
            # keeps a direct cluster command safe during a rolling deployment;
            # the normal refresh runs ``retag`` first and removes that ambiguity.
            items = [item for item in items
                     if (bool(item["cfb_eligible"])
                         if item.get("cfb_eligible") is not None
                         else _is_cfb_relevant(item))]

            clusters = _build_clusters(items)

            connection.execute("DELETE FROM story_items"); connection.execute("DELETE FROM story_teams")
            connection.execute("DELETE FROM story_players"); connection.execute("DELETE FROM story_games")
            connection.execute("DELETE FROM stories"); now=datetime.now(timezone.utc).isoformat()
            multi=0
            for key,method,group in clusters:
                if len(group)>1: multi+=1
                group.sort(key=lambda item:item["published_at"])
                topics=set().union(*(item["topics"] for item in group))
                # Role names were renamed when role determination was rebuilt;
                # this list matched nothing, so no story ever chose a reporting
                # primary or marked an original-report candidate.
                candidates=[item for item in group
                    if item["source_role"] in REPORTING_ROLES and item["reliability_score"]>=4]
                official=[item for item in group if item["source_role"]=="OFFICIAL_CONFIRMATION"]
                primary=(candidates or official or group)[0]; confidence=(0.9 if method=="SHARED_ARTICLE" and len(group)>1
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
                    elif item["source_role"] in REPORTING_ROLES: role="CORROBORATION_CANDIDATE"; role_conf=.6
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
                item["sources"]=[label_linked_piece(dict(r)) for r in connection.execute("""SELECT si.source_role,si.role_confidence,
                  si.is_primary,c.platform,c.content_type,c.canonical_url,c.original_url,
                  c.body_text,c.title,c.published_at,c.publisher_name,c.author_name,
                  e.name source_name
                  FROM story_items si JOIN content_items c USING(content_id)
                  LEFT JOIN source_entities e USING(source_entity_id) WHERE si.story_id=?
                  ORDER BY si.is_primary DESC,c.published_at""",(sid,))]
                primary = item["sources"][0] if item["sources"] else {}
                item["url"] = primary.get("original_url") or primary.get("canonical_url")
                item["source_name"] = primary.get("source_display_name") or "Source"
                for key in ("platform", "source_icon", "source_type_label", "makes_sound",
                            "interaction_label", "published_at", "published_exact",
                            "published_relative", "published_datetime", "source_display_name"):
                    item[key] = primary.get(key)
                item["teams"]=[dict(r) for r in connection.execute(
                  """SELECT t.team_id,t.school,t.conference,t.color,t.logos_json
                     FROM story_teams st JOIN teams t USING(team_id) WHERE story_id=?""",(sid,))]
                for team in item["teams"]:
                    logos=json.loads(team.pop("logos_json") or "[]")
                    team["logo"],team["logo_dark"]=_logo_pair(logos)
                    team["accent"]=readable_accent(team.get("color"))
                    team["accent_dark"]=dark_accent(team.get("color"))
                    team["conference_color"]=conference_color(team.get("conference"))
                    team["conference_color_dark"]=conference_color_dark(team.get("conference"))
                # The first resolved team paints the block, so a reader can scan a
                # long list by color before reading a single headline.
                primary=item["teams"][0] if item["teams"] else {}
                item["accent"]=primary.get("accent")
                item["accent_dark"]=primary.get("accent_dark")
                item["primary_team"]=primary.get("school")
                item["primary_logo"]=primary.get("logo")
                result.append(item)
        return result
