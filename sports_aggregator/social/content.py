"""Durable cross-platform content with conservative CFB entity candidates."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse
from typing import Any

from sports_aggregator.cfb.identity import readable_accent
from sports_aggregator.cfb.models import normalize_alias, normalize_person_name
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.models import Article
from sports_aggregator.social.context import (
    allows_unscoped_match, names_staff, transfer_role)
from sports_aggregator.social.relevance import score_item
from sports_aggregator.social.roles import determine_role
from sports_aggregator.social.unified import UnifiedSourceRegistry


CONTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS content_items (
 content_id INTEGER PRIMARY KEY AUTOINCREMENT,
 platform TEXT NOT NULL, platform_content_id TEXT NOT NULL,
 platform_cid TEXT, source_entity_id INTEGER, source_endpoint_id INTEGER,
 canonical_url TEXT, original_url TEXT, title TEXT NOT NULL DEFAULT '',
 body_text TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
 author_name TEXT NOT NULL DEFAULT '', publisher_name TEXT NOT NULL DEFAULT '',
 published_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
 content_type TEXT NOT NULL, source_role TEXT NOT NULL,
 is_repost INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL,
 UNIQUE(platform,platform_content_id),
 FOREIGN KEY(source_entity_id) REFERENCES source_entities(source_entity_id) ON DELETE SET NULL,
 FOREIGN KEY(source_endpoint_id) REFERENCES source_endpoints(endpoint_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_content_recent ON content_items(published_at DESC);
CREATE TABLE IF NOT EXISTS content_topics (
 content_id INTEGER NOT NULL, topic TEXT NOT NULL, confidence REAL NOT NULL,
 method TEXT NOT NULL, PRIMARY KEY(content_id,topic),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_teams (
 content_id INTEGER NOT NULL, team_id INTEGER NOT NULL, confidence REAL NOT NULL,
 method TEXT NOT NULL, PRIMARY KEY(content_id,team_id),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_players (
 content_id INTEGER NOT NULL, season INTEGER NOT NULL, player_id TEXT NOT NULL,
 confidence REAL NOT NULL, method TEXT NOT NULL,
 PRIMARY KEY(content_id,season,player_id),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_games (
 content_id INTEGER NOT NULL, game_id INTEGER NOT NULL, game_match_score REAL NOT NULL,
 method TEXT NOT NULL, PRIMARY KEY(content_id,game_id),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_links (
 content_id INTEGER NOT NULL, url TEXT NOT NULL, link_type TEXT NOT NULL,
 PRIMARY KEY(content_id,url),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_conferences (
 content_id INTEGER NOT NULL, conference TEXT NOT NULL, confidence REAL NOT NULL,
 method TEXT NOT NULL, PRIMARY KEY(content_id,conference),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_roles (
 content_id INTEGER PRIMARY KEY, role TEXT NOT NULL, confidence REAL NOT NULL,
 evidence_json TEXT NOT NULL, decided_at TEXT NOT NULL,
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_tag_evidence (
 content_id INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
 matched_text TEXT NOT NULL, location TEXT NOT NULL, method TEXT NOT NULL,
 confidence REAL NOT NULL, PRIMARY KEY(content_id,kind,target,matched_text),
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS content_relevance (
 content_id INTEGER PRIMARY KEY, score REAL NOT NULL, topic TEXT,
 importance REAL NOT NULL, recency REAL NOT NULL, expertise REAL NOT NULL,
 specificity REAL NOT NULL, factors_json TEXT NOT NULL, scored_at TEXT NOT NULL,
 FOREIGN KEY(content_id) REFERENCES content_items(content_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_content_relevance_score ON content_relevance(score DESC);
CREATE TABLE IF NOT EXISTS content_ingestion_runs (
 run_id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 endpoints_attempted INTEGER NOT NULL, endpoints_succeeded INTEGER NOT NULL,
 items_seen INTEGER NOT NULL, items_stored INTEGER NOT NULL,
 errors_json TEXT NOT NULL
);
"""


TOPIC_PATTERNS = {
    "INJURY": (r"\binjur", r"\bquestionable\b", r"\bdid not practice\b", r"\bmiss(?:ed|ing) practice\b"),
    "DEPTH_CHART": (r"\bdepth chart\b", r"\bstarter\b", r"\bstarting (?:quarterback|lineup)\b"),
    "ROSTER": (r"\broster\b", r"\bavailability\b"),
    "TRANSFER_PORTAL": (r"\btransfer portal\b", r"\bentered the portal\b", r"\bportal commitment\b"),
    "RECRUITING": (r"\brecruit", r"\bcommit(?:ted|ment)\b", r"\bsigning day\b"),
    "GAME_PREVIEW": (r"\bpreview\b", r"\bmatchup\b", r"\bkeys to (?:the )?game\b"),
    "GAME_RECAP": (r"\brecap\b", r"\bpostgame\b"),
    "STATISTICAL_ANALYSIS": (r"\bsp\+\b", r"\bprojection", r"\bppa\b", r"\bsuccess rate\b", r"\befficiency\b"),
    "SCHEME_ANALYSIS": (r"\bscheme\b", r"\bcoverage\b", r"\bpass rush\b", r"\bfilm (?:study|review|breakdown)\b"),
    "PLAYER_ANALYSIS": (r"\bplayer analysis\b", r"\bbreakout player\b"),
    "NFL_DRAFT": (r"\bnfl draft\b", r"\bdraft prospect\b", r"\bmock draft\b", r"\bsenior bowl\b"),
    "AWARDS": (r"\bheisman\b", r"\bsemifinalist", r"\bfinalist", r"\bwatch list\b"),
    "RANKINGS": (r"\brankings?\b", r"\btop 25\b", r"\bap poll\b"),
    "PLAYOFF": (r"\bplayoff\b", r"\bcfp\b"),
    "COACHING": (r"\bhead coach\b", r"\bcoaching (?:search|change|staff)\b", r"\bcoordinator\b"),
    "CONFERENCE": (r"\bconference\b", r"\brealignment\b"),
    "GOVERNANCE": (r"\bncaa\b", r"\bgovernance\b", r"\bcommissioner\b"),
    "NIL": (r"\bnil\b", r"\brevenue sharing\b", r"\bcollective\b"),
    "MEDIA": (r"\bmedia rights\b", r"\btv ratings\b", r"\bbroadcast\b"),
    # Added after auditing coverage: more than half of stored items matched
    # no topic at all, which left the relevance model nothing to weigh.
    "BETTING": (r"\bspread\b", r"\bover/under\b", r"\bmoneyline\b", r"\bodds\b",
                r"\bwin total", r"\bbest bets?\b", r"\bcover the spread\b"),
    "SCHEDULE": (r"\bschedule\b", r"\bkickoff time\b", r"\bnon-conference\b",
                 r"\bbye week\b", r"\bhome-and-home\b", r"\bseason opener\b"),
    "FACILITIES": (r"\bstadium\b", r"\brenovation\b", r"\bfacility\b",
                   r"\battendance\b", r"\bcrowd\b"),
    "OFFSEASON": (r"\bspring (?:game|practice|ball)\b", r"\bfall camp\b",
                  r"\bpreseason\b", r"\bcamp (?:battle|report|notes|intel)\b",
                  r"\boffseason\b", r"\bpractice report\b"),
    "BOWL": (r"\bbowl (?:game|projection|season|eligible)\b", r"\bbowl bid\b",
             r"\bpostseason\b"),
    "SEASON_PREVIEW": (r"\bseason preview\b", r"\bwin total", r"\bprojection",
                       r"\boutlook\b", r"\bwhat to expect\b", r"\bpredictions?\b"),
    "DISCIPLINE": (r"\bsuspend(?:ed|s|sion)\b", r"\bdismissed?\b",
                   r"\barrest(?:ed)?\b", r"\bviolation\b", r"\binvestigation\b"),
    "COMMENTARY": (r"\bopinion\b", r"\btakeaways?\b", r"\bthoughts on\b",
                   r"\breaction\b", r"\bmailbag\b", r"\bround ?table\b"),
}


def classify_topics(text: str) -> list[tuple[str, float, str]]:
    normalized = text.casefold(); topics = []
    for topic, patterns in TOPIC_PATTERNS.items():
        matches = sum(bool(re.search(pattern, normalized)) for pattern in patterns)
        if matches:
            topics.append((topic, min(0.95, 0.7 + 0.1 * (matches - 1)), "keyword_v1"))
    return topics


#: Reddit content types, checked in order. The first match wins, so structural
#: threads are recognised before the generic self-post and link fallbacks.
REDDIT_TITLE_RULES = (
    ("POSTGAME_THREAD", r"^\s*\[?\s*post[- ]?game thread"),
    ("GAME_THREAD", r"^\s*\[?\s*(?:game|pregame) thread"),
    ("QUESTION", r"\?\s*$"),
    ("RUMOR", r"rumor|hearing that|word is"),
)

#: Flair text that identifies a submission more reliably than its title.
REDDIT_FLAIR_RULES = (
    ("ANALYSIS", r"analysis|data|research|study|chart"),
    ("SCOUTING_OPINION", r"scouting|film|evaluation|prospect"),
    ("RESOURCE", r"resource|tool|database|spreadsheet"),
    ("GAME_THREAD", r"game thread"),
    ("RUMOR", r"rumor"),
)


def links_externally(submission: dict) -> bool:
    """True only when the submission points at a publisher outside Reddit.

    Crossposts, galleries, and image posts all report ``is_self=False`` while
    still pointing back at reddit.com, and must not be credited as discovery of
    an outside story.
    """
    url = str(submission.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return False
    host = urlparse(url).netloc.casefold()
    return not (host.endswith("reddit.com") or host.endswith("redd.it"))


def reddit_content_type(submission: dict, community_type: str | None = None) -> str:
    """Classify a submission so community chatter is never read as reporting."""
    title = str(submission.get("title") or "")
    flair = str(submission.get("link_flair_text") or "")
    for content_type, pattern in REDDIT_FLAIR_RULES:
        if flair and re.search(pattern, flair, re.I):
            return content_type
    for content_type, pattern in REDDIT_TITLE_RULES:
        if re.search(pattern, title, re.I):
            return content_type
    if links_externally(submission):
        return "LINK_DISCOVERY"
    if community_type == "ANALYTICS":
        return "ANALYSIS"
    if community_type == "DRAFT":
        return "SCOUTING_OPINION"
    return "COMMUNITY_REACTION"


#: Reddit roles. A subreddit never earns reporting credit: a linked submission is
#: an aggregation pointing at the publisher that did the work.
REDDIT_ROLES = {
    "LINK_DISCOVERY": "AGGREGATION",
    "ANALYSIS": "ANALYSIS",
    "SCOUTING_OPINION": "ANALYSIS",
    "RESOURCE": "AGGREGATION",
}


#: Video roles, checked in order. Titles are the only reliable signal available
#: without captions, so the rules stay conservative and fall back to analysis.
VIDEO_TITLE_RULES = (
    ("PRESS_CONFERENCE", r"press conference|presser|media availability"),
    ("INTERVIEW", r"\binterview\b|sits down with|one[- ]on[- ]one"),
    ("FILM_BREAKDOWN", r"film (?:room|study|breakdown|review)|all[- ]22|tape breakdown"),
    ("GAME_PREVIEW", r"\bpreview\b|\bpredictions?\b|\bpicks\b|keys to the game|what to watch"),
    ("GAME_REACTION", r"\breaction\b|instant analysis|postgame|recap|takeaways"),
    ("DRAFT_ANALYSIS", r"\bnfl draft\b|draft prospect|mock draft|scouting report"),
    ("RECRUITING", r"\brecruit|\bcommit(?:s|ment|ted)?\b|signing day|transfer portal"),
    ("RANKINGS", r"\brankings?\b|top 25|power rankings|playoff picture"),
    ("HIGHLIGHTS", r"\bhighlights\b|\bfull game\b|\bcondensed game\b"),
)


def video_content_type(title: str, description: str = "") -> str:
    """Classify a video or episode from published metadata, never from assumption."""
    haystack = f"{title} {description[:400]}"
    for content_type, pattern in VIDEO_TITLE_RULES:
        if re.search(pattern, haystack, re.I):
            return content_type
    return "VIDEO_ANALYSIS"


#: Roles for episodic media. A show discussing a report is analysis; a press
#: conference is the institution speaking for itself.
VIDEO_ROLES = {
    "PRESS_CONFERENCE": "OFFICIAL_CONFIRMATION",
    "INTERVIEW": "REPORTING_UNDETERMINED",
    "HIGHLIGHTS": "AGGREGATION",
}


#: Promotional boilerplate that dominates video and podcast descriptions.
BOILERPLATE = re.compile(
    r"(?:^|\s)(?:subscribe|follow|like us|watch more|download the|check out)\b.*",
    re.I | re.S)
URL_PATTERN = re.compile(r"https?://\S+")
HASHTAG_RUN = re.compile(r"(?:#\w+\s*){2,}")


def display_text(item: dict[str, Any], limit: int = 180) -> str:
    """A readable headline for any content row.

    A video description is mostly subscribe links and hashtags, so rendering it
    as a headline produced a wall of promotional text. Titles win where they
    exist; otherwise the body is stripped of boilerplate and trimmed.
    """
    title = (item.get("title") or "").strip()
    if title:
        return title if len(title) <= limit else title[: limit - 1].rstrip() + "\u2026"
    body = (item.get("body_text") or item.get("summary") or "").strip()
    cleaned = " ".join(HASHTAG_RUN.sub(" ", URL_PATTERN.sub(" ", body)).split())
    trimmed = " ".join(BOILERPLATE.sub(" ", cleaned).split())
    # Cutting at the boilerplate can remove everything; keep the cleaned text
    # rather than reporting an item as untitled when it does have words.
    body = trimmed or cleaned
    if not body:
        return "Untitled item"
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "\u2026"


def display_timestamp(value: str | None) -> str:
    """Publication time as a short, readable label rather than a raw ISO string."""
    if not value:
        return "undated"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h ago"
    if hours < 24 * 7:
        return f"{int(hours // 24)}d ago"
    return parsed.strftime("%b %d")


def source_role(classes: set[str]) -> str:
    if classes & {"PRIMARY_SOURCE", "OFFICIAL_TEAM", "OFFICIAL_CONFERENCE", "OFFICIAL_AWARD"}:
        return "OFFICIAL_CONFIRMATION"
    if "BOT" in classes: return "AUTOMATED"
    if "AGGREGATOR" in classes: return "AGGREGATION"
    if classes & {"BEAT_REPORTER", "NATIONAL_REPORTER"}: return "REPORTING_UNDETERMINED"
    if classes & {"NATIONAL_ANALYST", "TEAM_ANALYST", "FILM_ANALYST", "DRAFT_ANALYST", "SCOUT", "MODEL"}:
        return "ANALYSIS"
    if "COMMUNITY" in classes: return "COMMUNITY_REACTION"
    return "UNCLASSIFIED"


def _post_url(handle: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rstrip('/').split('/')[-1]}"


def _external_links(post: dict) -> list[str]:
    links: set[str] = set(); record = post.get("record") or {}
    for facet in record.get("facets") or []:
        for feature in facet.get("features") or []:
            uri = feature.get("uri")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")): links.add(uri)
    embed = post.get("embed") or {}; external = embed.get("external") or {}
    uri = external.get("uri")
    if isinstance(uri, str) and uri.startswith(("http://", "https://")): links.add(uri)
    return sorted(links)


class ContentRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self._team_aliases: dict[str, set[int]] | None = None
        self._short_aliases: dict[str, set[int]] | None = None
        self._last_evidence: list[dict[str, Any]] = []
        self._players_by_season: dict[int, dict[str, list[dict]]] = {}
        self._games_by_season: dict[int, list[dict]] = {}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20); connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON"); return connection

    def initialize(self) -> None:
        CFBRepository(self.path).initialize()
        UnifiedSourceRegistry(self.path).initialize()
        with closing(self._connect()) as connection: connection.executescript(CONTENT_SCHEMA)

    def bluesky_endpoints(self) -> list[dict]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT ep.*,e.name,e.entity_type FROM source_endpoints ep
                   JOIN source_entities e USING(source_entity_id)
                   WHERE ep.platform='bluesky' AND ep.active=1
                   AND ep.verification_status='verified' AND ep.platform_id IS NOT NULL
                   ORDER BY e.priority DESC,e.name""").fetchall(); result=[]
            for row in rows:
                item=dict(row); item["classes"]={r[0] for r in connection.execute(
                    "SELECT source_class FROM source_entity_classes WHERE source_entity_id=?",
                    (row["source_entity_id"],))}; result.append(item)
        return result

    #: Above this many distinct teams, an item is a list or a roundup rather than
    #: a story about any one program, and every mention is demoted accordingly.
    LIST_MENTION_THRESHOLD = 6

    #: Characters treated as the lead. A team named here is the subject; a team
    #: named far down the body is usually a passing comparison.
    LEAD_LENGTH = 220

    def _alias_index(self, connection: sqlite3.Connection) -> tuple[dict, dict]:
        """Two alias indexes: long names for normalized text, short for raw text.

        Three-letter programs -- USC, LSU, BYU, TCU -- were previously unmatchable
        because a four-character floor filtered them out before comparison. They
        are matched instead against the original text in upper case, where "USC"
        is a deliberate reference and a lowercase "usc" inside another word is not.
        """
        if self._team_aliases is None:
            long_aliases: dict[str, set[int]] = defaultdict(set)
            short_aliases: dict[str, set[int]] = defaultdict(set)
            for row in connection.execute("SELECT team_id,normalized_alias FROM team_aliases"):
                alias = row["normalized_alias"]
                if len(alias) >= 4:
                    long_aliases[alias].add(row["team_id"])
                elif len(alias) >= 2:
                    short_aliases[alias.upper()].add(row["team_id"])
            self._team_aliases = dict(long_aliases)
            self._short_aliases = dict(short_aliases)
        return self._team_aliases, self._short_aliases

    #: Confidence for a team that a transfer story names only as the origin.
    ORIGIN_CONFIDENCE = 0.55

    def _team_candidates(self, connection: sqlite3.Connection, text: str,
                         source_entity_id: int | None = None,
                         title: str | None = None) -> list[tuple[int,float,str]]:
        """Resolve team mentions, weighted by prominence and demoted in lists.

        Three things separate a subject from a mention: whether the team is named
        in the headline, whether it sits in the lead, and whether a transfer story
        names it as the destination or merely as where the player came from.
        """
        normalized = f" {normalize_alias(text)} "
        lead_normalized = f" {normalize_alias(text[:self.LEAD_LENGTH])} "
        headline_normalized = f" {normalize_alias(title)} " if title else ""
        by_alias, short_by_alias = self._alias_index(connection)

        hits = [alias for alias, team_ids in by_alias.items()
                if len(team_ids) == 1 and f" {alias} " in normalized]
        # Drop an alias fully contained in a longer one that also matched, so
        # "Ohio" does not compete with "Ohio State" in the same sentence.
        hits = [alias for alias in hits if not any(
            alias != longer and f" {alias} " in f" {longer} " for longer in hits
        )]

        found: dict[int, tuple[float, str]] = {}
        # Evidence is kept alongside the decision so a page can say which words
        # produced a link rather than only that one exists.
        self._last_evidence = []
        for alias in hits:
            team_id = next(iter(by_alias[alias]))
            in_headline = bool(headline_normalized) and f" {alias} " in headline_normalized
            prominent = in_headline or f" {alias} " in lead_normalized
            confidence = 0.95 if " " in alias else 0.88
            if in_headline:
                # A team in the headline is what the item is about.
                confidence = min(0.98, confidence + 0.03)
            elif not prominent:
                confidence -= 0.15
            self._last_evidence.append({
                "kind": "team", "target": str(team_id), "matched_text": alias,
                "location": "lead" if prominent else "body",
                "method": "exact_team_alias", "confidence": round(confidence, 2)})
            if confidence > found.get(team_id, (0, ""))[0]:
                found[team_id] = (round(confidence, 2), "exact_team_alias")

        # Short abbreviations must appear as upper-case words in the original
        # text; that is what separates "USC" from an incidental letter run.
        lead_raw = text[:self.LEAD_LENGTH]
        for alias, team_ids in short_by_alias.items():
            if len(team_ids) != 1:
                continue
            if not re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text):
                continue
            team_id = next(iter(team_ids))
            prominent = bool(re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", lead_raw))
            confidence = 0.9 if prominent else 0.78
            self._last_evidence.append({
                "kind": "team", "target": str(team_id), "matched_text": alias,
                "location": "lead" if prominent else "body",
                "method": "team_abbreviation", "confidence": confidence})
            if confidence > found.get(team_id, (0, ""))[0]:
                found[team_id] = (confidence, "team_abbreviation")

        # Transfer stories name two schools; only the destination is the subject.
        schools = {row["team_id"]: row["school"] for row in connection.execute(
            "SELECT team_id,school FROM teams")} if len(found) > 1 else {}
        roles = {team_id: transfer_role(text, schools[team_id])
                 for team_id in found if team_id in schools}
        if "destination" in roles.values():
            for team_id, role in roles.items():
                if role == "destination":
                    confidence, _ = found[team_id]
                    found[team_id] = (min(0.98, confidence + 0.05), "transfer_destination")
                else:
                    found[team_id] = (self.ORIGIN_CONFIDENCE, "transfer_origin")

        if len(found) > self.LIST_MENTION_THRESHOLD:
            # A ranking or roundup names many programs without being about any of
            # them. The links are kept for search, but not at reporting strength.
            found = {team_id: (0.3, "list_mention") for team_id in found}
            for item in self._last_evidence:
                item["method"] = "list_mention"
                item["confidence"] = 0.3

        if source_entity_id is not None:
            for row in connection.execute(
                """SELECT t.team_id FROM source_entity_teams setm
                   JOIN teams t ON t.school=setm.team WHERE setm.source_entity_id=?""",
                (source_entity_id,),
            ):
                found.setdefault(row["team_id"], (0.65, "source_team_scope"))
        return [(team_id, *values) for team_id, values in found.items()]

    def _player_candidates(self, connection: sqlite3.Connection, text: str, season: int,
                           team_ids: set[int]) -> list[tuple[str,float,str]]:
        """Resolve player mentions by exact full name, scoped where possible.

        Scoping to the resolved teams is the strongest signal, but requiring it
        silently dropped players whose team was never named -- a column about
        Arch Manning that resolves other programmes still concerns him. A name
        that is unique across the whole roster therefore links at lower
        confidence, with the weaker method recorded.
        """
        # Person names are normalized with initials collapsed so "C.J. Carr" in
        # an article reaches "CJ Carr" on the roster.
        normalized = f" {normalize_person_name(text)} "
        team_names = {row[0] for row in connection.execute(
            f"SELECT school FROM teams WHERE team_id IN ({','.join('?' for _ in team_ids)})",
            tuple(team_ids))} if team_ids else set()
        if season not in self._players_by_season:
            by_name: dict[str, list[dict]] = defaultdict(list)
            for row in connection.execute(
                "SELECT player_id,first_name,last_name,normalized_name,team "
                "FROM players WHERE season=?", (season,)
            ):
                key = normalize_person_name(f"{row['first_name']} {row['last_name']}")
                if len(key) >= 7 and " " in key:
                    by_name[key].append(dict(row))
            self._players_by_season[season] = dict(by_name)
        by_name = self._players_by_season[season]
        # An unscoped match needs the item to look like college football and not
        # like the pro game, otherwise a shared name silently becomes a link.
        unscoped_allowed = allows_unscoped_match(text, has_resolved_team=bool(team_names))
        found = []
        for name, rows in by_name.items():
            if f" {name} " not in normalized:
                continue
            on_resolved_team = [row for row in rows if row["team"] in team_names]
            if len(on_resolved_team) == 1:
                player, confidence = on_resolved_team[0], 0.95
                method = "exact_full_name_on_resolved_team"
            elif len(rows) == 1 and unscoped_allowed and not names_staff(text, name):
                # Unique across every roster, but the team was not resolved from
                # the text. Still the same person; simply less corroborated.
                player, confidence = rows[0], 0.72
                method = "exact_full_name_unscoped"
            else:
                continue
            found.append((player["player_id"], confidence, method))
            self._last_evidence.append({
                "kind": "player", "target": player["player_id"], "matched_text": name,
                "location": "body", "method": method, "confidence": confidence})
        return found

    def _game_candidates(self, connection: sqlite3.Connection, team_ids: set[int], season: int) -> list[tuple[int,float,str]]:
        if not team_ids: return []
        now=datetime.now(timezone.utc); cutoff=(now+timedelta(days=21)).isoformat()
        if season not in self._games_by_season:
            self._games_by_season[season] = [dict(row) for row in connection.execute(
                """SELECT game_id,home_team_id,away_team_id,start_date FROM games
                   WHERE season=? AND completed=0 AND start_date BETWEEN ? AND ? ORDER BY start_date""",
                (season,now.isoformat(),cutoff)).fetchall()]
        rows=self._games_by_season[season]
        exact=[row for row in rows if {row["home_team_id"],row["away_team_id"]}.issubset(team_ids)]
        if len(exact)==1: return [(exact[0]["game_id"],1.0,"both_teams_upcoming_game")]
        if len(team_ids)==1:
            team_id=next(iter(team_ids)); one=[row for row in rows if team_id in {row["home_team_id"],row["away_team_id"]}]
            if one: return [(one[0]["game_id"],0.55,"next_game_single_team")]
        return []

    def _link_entities(self, connection: sqlite3.Connection, content_id: int, text: str,
                       entity_id: int | None, season: int,
                       title: str | None = None) -> set[int]:
        """Attach topic, team, player, and game candidates to one content row.

        Every ingestion path uses this, so a Reddit submission and a Bluesky post
        resolve entities by the same conservative rules and record the same
        confidence and method for review.
        """
        for table in ("content_topics", "content_teams", "content_players", "content_games"):
            connection.execute(f"DELETE FROM {table} WHERE content_id=?", (content_id,))
        connection.executemany("INSERT INTO content_topics VALUES(?,?,?,?)",
                               [(content_id, *item) for item in classify_topics(text)])
        connection.execute("DELETE FROM content_tag_evidence WHERE content_id=?", (content_id,))
        teams = self._team_candidates(connection, text, entity_id, title=title)
        team_ids = {item[0] for item in teams}
        # Only confidently-resolved teams may drive player and game resolution;
        # a passing mention in a ranking post should not schedule a game link.
        confident_ids = {item[0] for item in teams if item[1] >= 0.75}
        connection.executemany("INSERT INTO content_teams VALUES(?,?,?,?)",
                               [(content_id, *item) for item in teams])
        connection.executemany(
            "INSERT INTO content_players VALUES(?,?,?,?,?)",
            [(content_id, season, *item)
             for item in self._player_candidates(connection, text, season, team_ids)])
        # Evidence is written once, after every resolver has contributed to it.
        # Writing it straight after team resolution silently discarded every
        # player match, because those are appended later in this same method.
        connection.executemany(
            "INSERT OR REPLACE INTO content_tag_evidence VALUES(?,?,?,?,?,?,?)",
            [(content_id, item["kind"], item["target"], item["matched_text"],
              item["location"], item["method"], item["confidence"])
             for item in self._last_evidence])
        connection.executemany(
            "INSERT INTO content_games VALUES(?,?,?,?)",
            [(content_id, *item)
             for item in self._game_candidates(connection, confident_ids, season)])
        connection.execute("DELETE FROM content_conferences WHERE content_id=?", (content_id,))
        connection.executemany(
            "INSERT OR IGNORE INTO content_conferences VALUES(?,?,?,?)",
            [(content_id, *item)
             for item in self._conference_candidates(connection, confident_ids, entity_id)])
        return team_ids

    def _conference_candidates(self, connection: sqlite3.Connection, team_ids: set[int],
                               entity_id: int | None) -> list[tuple[str, float, str]]:
        """Derive conferences from resolved teams, then from the source beat.

        Conferences are never matched from free text: "Big Ten" appears in far
        too much national copy that concerns one specific team.
        """
        found: dict[str, tuple[float, str]] = {}
        if team_ids:
            placeholders = ",".join("?" for _ in team_ids)
            for row in connection.execute(
                f"SELECT DISTINCT conference FROM teams WHERE team_id IN ({placeholders}) "
                "AND conference IS NOT NULL", tuple(team_ids)
            ):
                found[row["conference"]] = (0.9, "resolved_team_conference")
        if entity_id is not None:
            for row in connection.execute(
                "SELECT conference FROM source_entity_conferences WHERE source_entity_id=?",
                (entity_id,),
            ):
                name = row["conference"]
                if name and name.upper() != "ALL":
                    found.setdefault(name, (0.5, "source_conference_scope"))
        return [(name, *values) for name, values in found.items()]

    def retag(self, season: int) -> dict[str, Any]:
        """Re-resolve topics and entities for stored content from its saved text.

        Tagging rules change more often than the content does. Re-running the
        resolvers over what is already stored lets a rule improvement reach the
        whole archive without re-fetching any source, and keeps the confidence
        and method columns consistent across ingestion dates.
        """
        self.initialize()
        self._team_aliases = None
        self._short_aliases = None
        self._players_by_season = {}
        self._games_by_season = {}
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT content_id,source_entity_id,title,body_text,summary
                   FROM content_items ORDER BY content_id""").fetchall()
            counts = {"items": 0, "teams": 0, "players": 0, "games": 0, "conferences": 0}
            for row in rows:
                text = " ".join(filter(None, (row["title"], row["body_text"], row["summary"])))
                self._link_entities(connection, row["content_id"], text,
                                    row["source_entity_id"], season,
                                    title=row["title"])
                counts["items"] += 1
            connection.commit()
            for table, key in (("content_teams", "teams"), ("content_players", "players"),
                               ("content_games", "games"), ("content_conferences", "conferences")):
                counts[key] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts

    def redetermine_roles(self) -> dict[str, Any]:
        """Re-classify every stored item and record the evidence for each verdict.

        Roles were previously fixed at ingestion from the source class alone, so
        every journalist's post became REPORTING_UNDETERMINED regardless of what
        it said. Determination now reads the stored text, and because it is a
        separate pass the rules can improve without re-fetching any source.
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            classes_by_entity: dict[int, set[str]] = defaultdict(set)
            for row in connection.execute(
                "SELECT source_entity_id,source_class FROM source_entity_classes"
            ):
                classes_by_entity[row["source_entity_id"]].add(row["source_class"])
            external_ids = {row[0] for row in connection.execute(
                "SELECT DISTINCT content_id FROM content_links WHERE link_type='ORIGINAL'")}
            # Position inside a story cluster, so a later item reads as corroboration.
            position: dict[int, tuple[int, int]] = {}
            try:
                cluster_rows = connection.execute(
                    """SELECT si.story_id,si.content_id,c.published_at FROM story_items si
                       JOIN content_items c USING(content_id) ORDER BY si.story_id,c.published_at"""
                ).fetchall()
            except sqlite3.OperationalError:
                cluster_rows = []
            grouped: dict[int, list[int]] = defaultdict(list)
            for row in cluster_rows:
                grouped[row["story_id"]].append(row["content_id"])
            for members in grouped.values():
                for index, content_id in enumerate(members, start=1):
                    position[content_id] = (index, len(members))
            rows = connection.execute(
                """SELECT content_id,platform,title,body_text,summary,content_type,
                   source_entity_id FROM content_items""").fetchall()
            counts: dict[str, int] = defaultdict(int)
            for row in rows:
                text = " ".join(filter(None, (row["title"], row["body_text"], row["summary"])))
                index, size = position.get(row["content_id"], (None, 1))
                verdict = determine_role(
                    text=text, content_type=row["content_type"],
                    classes=classes_by_entity.get(row["source_entity_id"], set()),
                    platform=row["platform"],
                    links_external=row["content_id"] in external_ids,
                    cluster_position=index, cluster_size=size,
                )
                connection.execute(
                    """INSERT INTO content_roles VALUES(?,?,?,?,?)
                       ON CONFLICT(content_id) DO UPDATE SET role=excluded.role,
                       confidence=excluded.confidence,evidence_json=excluded.evidence_json,
                       decided_at=excluded.decided_at""",
                    (row["content_id"], verdict["role"], verdict["confidence"],
                     json.dumps(verdict["evidence"], separators=(",", ":")), now))
                connection.execute("UPDATE content_items SET source_role=? WHERE content_id=?",
                                   (verdict["role"], row["content_id"]))
                counts[verdict["role"]] += 1
            connection.commit()
        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    def rescore(self, limit: int | None = None) -> dict[str, Any]:
        """Recompute relevance for stored content and persist the breakdown.

        Scoring is a separate pass rather than an ingestion side effect: recency
        decays continuously, and the weights are expected to be retuned once
        classification precision has been measured.
        """
        self.initialize()
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT c.content_id,c.published_at,c.source_role,c.content_type,
                   c.source_entity_id,e.reliability_score,e.reporting_score,e.team_access_score,
                   e.national_score,e.analytics_score,e.scheme_score,e.recruiting_score,
                   e.transfer_score,e.draft_score,e.awards_score,e.g5_score,e.breaking_score,
                   e.official_score,
                   (SELECT MAX(confidence) FROM content_teams WHERE content_id=c.content_id) team_confidence,
                   (SELECT MAX(confidence) FROM content_players WHERE content_id=c.content_id) player_confidence,
                   (SELECT MAX(game_match_score) FROM content_games WHERE content_id=c.content_id) game_score
                   FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
                   ORDER BY c.published_at DESC""" + (" LIMIT ?" if limit else ""),
                (limit,) if limit else ()).fetchall()
            topics_by_content: dict[int, list[str]] = defaultdict(list)
            for row in connection.execute("SELECT content_id,topic FROM content_topics"):
                topics_by_content[row["content_id"]].append(row["topic"])
            teams_by_content: dict[int, list[int]] = defaultdict(list)
            for row in connection.execute("SELECT content_id,team_id FROM content_teams"):
                teams_by_content[row["content_id"]].append(row["team_id"])
            beats_by_entity: dict[int, list[int]] = defaultdict(list)
            for row in connection.execute(
                """SELECT setm.source_entity_id,t.team_id FROM source_entity_teams setm
                   JOIN teams t ON t.school=setm.team"""
            ):
                beats_by_entity[row["source_entity_id"]].append(row["team_id"])
            scored = 0
            for row in rows:
                item = dict(row)
                entity = {key: item.get(key) for key in (
                    "reliability_score", "reporting_score", "team_access_score", "national_score",
                    "analytics_score", "scheme_score", "recruiting_score", "transfer_score",
                    "draft_score", "awards_score", "g5_score", "breaking_score", "official_score")}
                result = score_item({
                    **item,
                    "entity": entity if item.get("source_entity_id") else None,
                    "topics": topics_by_content.get(item["content_id"], []),
                    "content_team_ids": teams_by_content.get(item["content_id"], []),
                    "entity_team_ids": beats_by_entity.get(item.get("source_entity_id"), []),
                }, now=now)
                connection.execute(
                    """INSERT INTO content_relevance VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(content_id) DO UPDATE SET score=excluded.score,
                       topic=excluded.topic,importance=excluded.importance,recency=excluded.recency,
                       expertise=excluded.expertise,specificity=excluded.specificity,
                       factors_json=excluded.factors_json,scored_at=excluded.scored_at""",
                    (item["content_id"], result["score"], result["topic"], result["importance"],
                     result["recency"], result["expertise"], result["specificity"],
                     json.dumps(result["factors"], separators=(",", ":")), now.isoformat()),
                )
                scored += 1
            connection.commit()
        return {"scored": scored}

    def reddit_endpoints(self) -> list[dict]:
        """Verified subreddit endpoints with their curated community metadata."""
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT ep.*,e.name,e.entity_type,rc.community_type,rc.quality_score,
                   rc.reporting_authority,rc.original_source_extraction
                   FROM source_endpoints ep JOIN source_entities e USING(source_entity_id)
                   LEFT JOIN reddit_communities rc ON rc.endpoint_id=ep.endpoint_id
                   WHERE ep.platform='reddit' AND ep.active=1
                   AND ep.verification_status='verified'
                   ORDER BY rc.quality_score DESC,e.name""").fetchall()
        return [dict(row) for row in rows]

    def _episodic_text(self, item, key: str, default: str = "") -> str:
        """Read a field from either a dataclass provider record or a plain dict."""
        value = getattr(item, key, None)
        if value is None and isinstance(item, dict):
            value = item.get(key)
        return default if value is None else value

    def store_youtube_video(self, endpoint: dict, video: Any, season: int) -> int | None:
        """Store one video using its stable video ID, classified by metadata."""
        self.initialize()
        video_id = str(self._episodic_text(video, "video_id")).strip()
        title = str(self._episodic_text(video, "title")).strip()
        if not video_id or not title:
            return None
        description = str(self._episodic_text(video, "description"))
        published = self._episodic_text(video, "published_at", "")
        published_at = published.isoformat() if hasattr(published, "isoformat") else str(published)
        if not published_at:
            return None
        url = str(self._episodic_text(video, "url")) or f"https://www.youtube.com/watch?v={video_id}"
        duration = str(self._episodic_text(video, "duration"))
        content_type = video_content_type(title, description)
        role = VIDEO_ROLES.get(content_type) or source_role(endpoint.get("classes") or set())
        if role == "UNCLASSIFIED":
            role = "ANALYSIS"
        now = datetime.now(timezone.utc).isoformat()
        payload = {"video_id": video_id, "duration": duration,
                   "channel_id": endpoint.get("platform_id")}
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,source_entity_id,
                   source_endpoint_id,canonical_url,original_url,title,body_text,summary,
                   author_name,publisher_name,published_at,ingested_at,content_type,source_role,
                   is_repost,raw_json) VALUES('youtube',?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(platform,platform_content_id) DO UPDATE SET
                   canonical_url=excluded.canonical_url,title=excluded.title,
                   body_text=excluded.body_text,summary=excluded.summary,
                   published_at=excluded.published_at,ingested_at=excluded.ingested_at,
                   content_type=excluded.content_type,source_role=excluded.source_role,
                   raw_json=excluded.raw_json""",
                (video_id, endpoint.get("source_entity_id"), endpoint.get("endpoint_id"),
                 url, url, title, description[:4000], description[:400],
                 endpoint.get("display_name") or endpoint.get("name") or "",
                 endpoint.get("name") or "", published_at, now, content_type, role,
                 json.dumps(payload, separators=(",", ":"))),
            )
            content_id = connection.execute(
                "SELECT content_id FROM content_items WHERE platform='youtube' AND platform_content_id=?",
                (video_id,)).fetchone()[0]
            self._link_entities(connection, content_id, f"{title} {description[:1500]}",
                                endpoint.get("source_entity_id"), season, title=title)
            connection.execute("DELETE FROM content_links WHERE content_id=?", (content_id,))
            connection.execute("INSERT INTO content_links VALUES(?,?,'ORIGINAL')", (content_id, url))
            connection.commit()
        return content_id

    def store_podcast_episode(self, endpoint: dict, episode: Any, season: int) -> int | None:
        """Store one episode keyed by its GUID, which is stable across feed edits."""
        self.initialize()
        episode_id = str(self._episodic_text(episode, "episode_id")).strip()
        title = str(self._episodic_text(episode, "title")).strip()
        if not episode_id or not title:
            return None
        description = str(self._episodic_text(episode, "description"))
        published = self._episodic_text(episode, "published_at", "")
        published_at = published.isoformat() if hasattr(published, "isoformat") else str(published)
        if not published_at:
            return None
        page_url = str(self._episodic_text(episode, "page_url"))
        audio_url = str(self._episodic_text(episode, "audio_url"))
        content_type = video_content_type(title, description)
        role = source_role(endpoint.get("classes") or set())
        if role == "UNCLASSIFIED":
            role = "ANALYSIS"
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,source_entity_id,
                   source_endpoint_id,canonical_url,original_url,title,body_text,summary,
                   author_name,publisher_name,published_at,ingested_at,content_type,source_role,
                   is_repost,raw_json) VALUES('podcast',?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(platform,platform_content_id) DO UPDATE SET
                   canonical_url=excluded.canonical_url,title=excluded.title,
                   body_text=excluded.body_text,summary=excluded.summary,
                   published_at=excluded.published_at,ingested_at=excluded.ingested_at,
                   content_type=excluded.content_type,raw_json=excluded.raw_json""",
                (episode_id, endpoint.get("source_entity_id"), endpoint.get("endpoint_id"),
                 page_url or audio_url, page_url or audio_url, title, description[:4000],
                 description[:400], endpoint.get("name") or "", endpoint.get("name") or "",
                 published_at, now, content_type, role,
                 json.dumps({"audio_url": audio_url}, separators=(",", ":"))),
            )
            content_id = connection.execute(
                "SELECT content_id FROM content_items WHERE platform='podcast' AND platform_content_id=?",
                (episode_id,)).fetchone()[0]
            self._link_entities(connection, content_id, f"{title} {description[:1500]}",
                                endpoint.get("source_entity_id"), season, title=title)
            connection.commit()
        return content_id

    def store_reddit_submission(self, endpoint: dict, submission: dict, season: int) -> int | None:
        """Store one submission, crediting the linked publisher rather than Reddit.

        A submission that links out is recorded as discovery: the subreddit is the
        discovery endpoint, the external domain is the publisher, and the role is
        AGGREGATION. Nothing here converts community discussion into reporting.
        """
        self.initialize()
        identifier = str(submission.get("id") or "")
        if not identifier or submission.get("over_18"):
            return None
        title = str(submission.get("title") or "").strip()
        if not title:
            return None
        content_type = reddit_content_type(submission, endpoint.get("community_type"))
        body = str(submission.get("selftext") or "")
        classification_text = " ".join(filter(None, (title, body[:1500])))
        permalink = str(submission.get("permalink") or "")
        outbound = str(submission.get("url") or "")
        links_out = links_externally(submission)
        domain = str(submission.get("domain") or "") or urlparse(outbound).netloc
        published = datetime.fromtimestamp(
            float(submission.get("created_utc") or 0), tz=timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        role = REDDIT_ROLES.get(content_type, "COMMUNITY_REACTION")
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,source_entity_id,
                   source_endpoint_id,canonical_url,original_url,title,body_text,summary,
                   author_name,publisher_name,published_at,ingested_at,content_type,source_role,
                   is_repost,raw_json) VALUES('reddit',?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(platform,platform_content_id) DO UPDATE SET
                   canonical_url=excluded.canonical_url,original_url=excluded.original_url,
                   title=excluded.title,body_text=excluded.body_text,summary=excluded.summary,
                   publisher_name=excluded.publisher_name,published_at=excluded.published_at,
                   ingested_at=excluded.ingested_at,content_type=excluded.content_type,
                   source_role=excluded.source_role,raw_json=excluded.raw_json""",
                (identifier, endpoint["source_entity_id"], endpoint["endpoint_id"], permalink,
                 outbound if links_out else permalink, title, body[:4000], body[:400],
                 f"u/{submission.get('author')}", domain if links_out else f"r/{submission.get('subreddit')}",
                 published, now, content_type, role,
                 json.dumps(submission, separators=(",", ":"))),
            )
            content_id = connection.execute(
                "SELECT content_id FROM content_items WHERE platform='reddit' AND platform_content_id=?",
                (identifier,)).fetchone()[0]
            self._link_entities(connection, content_id, classification_text,
                                endpoint["source_entity_id"], season, title=title)
            connection.execute("DELETE FROM content_links WHERE content_id=?", (content_id,))
            if links_out:
                connection.execute("INSERT INTO content_links VALUES(?,?,'ORIGINAL')",
                                   (content_id, outbound))
            connection.execute("INSERT OR IGNORE INTO content_links VALUES(?,?,'DISCOVERY')",
                               (content_id, permalink))
            connection.commit()
        return content_id

    def store_bluesky_post(self, endpoint: dict, feed_item: dict, season: int) -> int | None:
        if feed_item.get("reason"):
            return None  # Repost suppression; originals may arrive from their own endpoint.
        post=feed_item.get("post") or {}; author=post.get("author") or {}; record=post.get("record") or {}
        if author.get("did") != endpoint["platform_id"]: return None
        uri=str(post.get("uri") or ""); created=str(record.get("createdAt") or "")
        if not uri or not created: return None
        handle=str(author.get("handle") or endpoint.get("handle") or ""); text=str(record.get("text") or "")
        external = (post.get("embed") or {}).get("external") or {}
        external_title = str(external.get("title") or "").strip()
        external_summary = str(external.get("description") or "").strip()
        classification_text = " ".join(filter(None, (text, external_title, external_summary)))
        now=datetime.now(timezone.utc).isoformat(); links=_external_links(post)
        with closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,platform_cid,source_entity_id,
                   source_endpoint_id,canonical_url,original_url,title,body_text,summary,author_name,publisher_name,
                   published_at,ingested_at,content_type,source_role,is_repost,raw_json)
                   VALUES('bluesky',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(platform,platform_content_id) DO UPDATE SET platform_cid=excluded.platform_cid,
                   canonical_url=excluded.canonical_url,original_url=excluded.original_url,
                   title=excluded.title,body_text=excluded.body_text,summary=excluded.summary,
                   author_name=excluded.author_name,published_at=excluded.published_at,
                   ingested_at=excluded.ingested_at,raw_json=excluded.raw_json""",
                (uri,post.get("cid"),endpoint["source_entity_id"],endpoint["endpoint_id"],
                 _post_url(handle,uri),links[0] if links else None,external_title,text,external_summary,
                 str(author.get("displayName") or handle),endpoint["name"],created,now,
                 "SOCIAL_POST",source_role(endpoint["classes"]),json.dumps(feed_item,separators=(",",":"))),
            )
            content_id=connection.execute(
                "SELECT content_id FROM content_items WHERE platform='bluesky' AND platform_content_id=?",(uri,)).fetchone()[0]
            for table in ("content_topics","content_teams","content_players","content_games","content_links"):
                connection.execute(f"DELETE FROM {table} WHERE content_id=?",(content_id,))
            connection.executemany("INSERT INTO content_topics VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in classify_topics(classification_text)])
            teams=self._team_candidates(connection,classification_text,endpoint["source_entity_id"]); team_ids={item[0] for item in teams}
            connection.executemany("INSERT INTO content_teams VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in teams])
            players=self._player_candidates(connection,classification_text,season,team_ids)
            connection.executemany("INSERT INTO content_players VALUES(?,?,?,?,?)",
                                   [(content_id,season,*item) for item in players])
            games=self._game_candidates(connection,team_ids,season)
            connection.executemany("INSERT INTO content_games VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in games])
            connection.executemany("INSERT INTO content_links VALUES(?,?,'EXTERNAL')",
                                   [(content_id,url) for url in links])
            connection.commit()
        return content_id

    def store_article(self, article: Article, season: int) -> int:
        self.initialize(); now = datetime.now(timezone.utc).isoformat()
        published = article.published_at.isoformat() if article.published_at else now
        text = " ".join(filter(None, (article.title, article.summary)))
        with closing(self._connect()) as connection:
            entity = connection.execute(
                "SELECT source_entity_id FROM source_entities WHERE entity_key=?",
                (article.source_entity_key,),
            ).fetchone() if article.source_entity_key else None
            endpoint = connection.execute(
                "SELECT endpoint_id,source_entity_id FROM source_endpoints WHERE endpoint_key=?",
                (article.source_endpoint_key,),
            ).fetchone() if article.source_endpoint_key else None
            entity_id = endpoint["source_entity_id"] if endpoint else (entity["source_entity_id"] if entity else None)
            endpoint_id = endpoint["endpoint_id"] if endpoint else None
            classes = {row[0] for row in connection.execute(
                "SELECT source_class FROM source_entity_classes WHERE source_entity_id=?",
                (entity_id,),
            )} if entity_id else set()
            role = source_role(classes)
            if role == "UNCLASSIFIED" and article.content_kind == "REPORTING":
                role = "REPORTING_UNDETERMINED"
            connection.execute(
                """INSERT INTO content_items(platform,platform_content_id,source_entity_id,
                   source_endpoint_id,canonical_url,original_url,title,body_text,summary,
                   author_name,publisher_name,published_at,ingested_at,content_type,source_role,
                   is_repost,raw_json) VALUES('rss',?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(platform,platform_content_id) DO UPDATE SET
                   canonical_url=excluded.canonical_url,original_url=excluded.original_url,
                   title=excluded.title,body_text=excluded.body_text,summary=excluded.summary,
                   author_name=excluded.author_name,publisher_name=excluded.publisher_name,
                   published_at=excluded.published_at,ingested_at=excluded.ingested_at,
                   source_role=excluded.source_role,raw_json=excluded.raw_json""",
                (article.identity, entity_id, endpoint_id, article.url,
                 article.original_url or article.url, article.title, article.summary,
                 article.summary, article.author, article.publisher or article.source,
                 published, now, article.content_kind, role,
                 json.dumps(article.to_dict(), separators=(",", ":"))),
            )
            content_id = connection.execute(
                "SELECT content_id FROM content_items WHERE platform='rss' AND platform_content_id=?",
                (article.identity,),
            ).fetchone()[0]
            for table in ("content_topics","content_teams","content_players","content_games","content_links"):
                connection.execute(f"DELETE FROM {table} WHERE content_id=?", (content_id,))
            connection.executemany("INSERT INTO content_topics VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in classify_topics(text)])
            teams = [(team_id, 1.0, "provider_entity") for team_id in article.team_ids]
            if not teams:
                teams = self._team_candidates(connection, text, entity_id,
                                              title=article.title)
            team_ids = {item[0] for item in teams}
            connection.executemany("INSERT INTO content_teams VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in teams])
            players = [(player_id, 1.0, "provider_entity") for player_id in article.player_ids]
            if not players:
                players = self._player_candidates(connection, text, season, team_ids)
            connection.executemany("INSERT INTO content_players VALUES(?,?,?,?,?)",
                                   [(content_id,season,*item) for item in players])
            games = [(game_id, 1.0, "provider_entity") for game_id in article.game_ids]
            if not games:
                games = self._game_candidates(connection, team_ids, season)
            connection.executemany("INSERT INTO content_games VALUES(?,?,?,?)",
                                   [(content_id,*item) for item in games])
            connection.execute("INSERT INTO content_links VALUES(?,?,'ORIGINAL')",
                               (content_id, article.original_url or article.url))
            connection.commit()
        return content_id

    def record_run(self, started: str, finished: str, attempted: int, succeeded: int,
                   seen: int, stored: int, errors: list[dict], platform: str = "bluesky") -> None:
        self.initialize()
        with closing(self._connect()) as connection:
            connection.execute("INSERT INTO content_ingestion_runs VALUES(NULL,?,?,?,?,?,?,?,?)",
                               (platform,started,finished,attempted,succeeded,seen,stored,json.dumps(errors)))
            connection.commit()

    def recent(self, limit: int = 50) -> list[dict[str,Any]]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows=connection.execute("""SELECT c.*,e.name source_entity_name,
              COALESCE(r.score,0) relevance_score,r.topic relevance_topic,r.factors_json
              FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
              LEFT JOIN content_relevance r ON r.content_id=c.content_id
              ORDER BY published_at DESC LIMIT ?""",(limit,)).fetchall(); result=[]
            for row in rows:
                item=dict(row); content_id=item["content_id"]; item.pop("raw_json",None)
                item["relevance_factors"]=json.loads(item.pop("factors_json") or "[]")
                item["topics"]=[r[0] for r in connection.execute("SELECT topic FROM content_topics WHERE content_id=? ORDER BY topic",(content_id,))]
                item["teams"]=[dict(r) for r in connection.execute("""SELECT ct.*,t.school FROM content_teams ct JOIN teams t USING(team_id) WHERE content_id=?""",(content_id,))]
                item["players"]=[dict(r) for r in connection.execute("SELECT * FROM content_players WHERE content_id=?",(content_id,))]
                item["games"]=[dict(r) for r in connection.execute("SELECT * FROM content_games WHERE content_id=?",(content_id,))]
                result.append(item)
        return result

    #: Reader-facing stream definitions. Each is a source *type*, not a vendor:
    #: what the reader wants is "video" or "community", not "the YouTube table".
    STREAM_DEFINITIONS = (
        ("reporting", "Reporting", "Beat and national reporters", ("bluesky",)),
        ("articles", "Articles", "Published journalism", ("rss",)),
        ("video", "Video", "Curated shows and analysis", ("youtube",)),
        ("podcasts", "Podcasts", "Episodes from verified feeds", ("podcast",)),
        ("community", "Community", "Discussion and link discovery", ("reddit",)),
    )

    def link_audit(self, *, kind: str = "player", method: str | None = None,
                   limit: int = 100) -> dict[str, Any]:
        """Every entity link with the text that produced it, for review.

        Resolution rules are only trustworthy if they can be checked, and a link
        is hard to find by browsing. This returns the matched text, the rule that
        fired, the confidence, and enough of the item to judge whether the match
        is right.
        """
        self.initialize()
        with closing(self._connect()) as connection:
            if kind == "player":
                sql = """SELECT cp.method,cp.confidence,cp.player_id target,
                         p.first_name||' '||p.last_name label,p.team,p.position,
                         i.content_id,i.platform,i.title,i.body_text,i.canonical_url,
                         e.name source_name
                         FROM content_players cp
                         LEFT JOIN players p ON p.player_id=cp.player_id AND p.season=cp.season
                         JOIN content_items i USING(content_id)
                         LEFT JOIN source_entities e USING(source_entity_id)"""
            else:
                sql = """SELECT ct.method,ct.confidence,ct.team_id target,
                         t.school label,t.conference team,NULL position,
                         i.content_id,i.platform,i.title,i.body_text,i.canonical_url,
                         e.name source_name
                         FROM content_teams ct
                         JOIN teams t USING(team_id)
                         JOIN content_items i USING(content_id)
                         LEFT JOIN source_entities e USING(source_entity_id)"""
            params: list[Any] = []
            if method:
                sql += " WHERE method=?"
                params.append(method)
            sql += " ORDER BY confidence DESC,label LIMIT ?"
            params.append(limit)
            rows = [dict(row) for row in connection.execute(sql, params)]
            evidence: dict[tuple[int, str], list[dict]] = defaultdict(list)
            for row in connection.execute(
                "SELECT content_id,kind,target,matched_text,location,method,confidence "
                "FROM content_tag_evidence WHERE kind=?", (kind,)
            ):
                evidence[(row["content_id"], row["target"])].append(dict(row))
            counts = dict(connection.execute(
                f"SELECT method,COUNT(*) FROM content_{'players' if kind == 'player' else 'teams'} "
                "GROUP BY 1").fetchall())
        for row in rows:
            row["headline"] = display_text(row, limit=130)
            row["excerpt"] = " ".join(
                (row.get("body_text") or "")[:220].split()) or row["headline"]
            row["evidence"] = evidence.get((row["content_id"], str(row["target"])), [])
            row.pop("body_text", None)
        return {"kind": kind, "method": method, "counts": counts,
                "count": len(rows), "links": rows}

    def summary(self) -> dict[str, Any]:
        """Counts for the dashboard header: how much has been ingested and linked."""
        self.initialize()
        with closing(self._connect()) as connection:
            return {
                "total": connection.execute("SELECT COUNT(*) FROM content_items").fetchone()[0],
                "platforms": connection.execute(
                    "SELECT COUNT(DISTINCT platform) FROM content_items").fetchone()[0],
                "team_links": connection.execute(
                    "SELECT COUNT(*) FROM content_teams").fetchone()[0],
                "player_links": connection.execute(
                    "SELECT COUNT(*) FROM content_players").fetchone()[0],
                "game_links": connection.execute(
                    "SELECT COUNT(*) FROM content_games").fetchone()[0],
            }

    def source_streams(self, *, limit: int = 8, days: int = 10) -> list[dict[str, Any]]:
        """Recent content grouped by source type, each ranked by relevance.

        The ranked-developments table answers "what matters most". These streams
        answer "what is each kind of source saying", which is a different
        question and deserves its own column rather than being blended away.
        """
        self.initialize()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        streams = []
        with closing(self._connect()) as connection:
            for key, label, description, platforms in self.STREAM_DEFINITIONS:
                placeholders = ",".join("?" for _ in platforms)
                rows = connection.execute(
                    f"""SELECT c.content_id,c.platform,c.title,c.body_text,c.canonical_url,
                        c.publisher_name,c.author_name,c.published_at,c.content_type,
                        c.source_role,e.name source_entity_name,
                        COALESCE(r.score,0) score,r.topic
                        FROM content_items c
                        LEFT JOIN source_entities e USING(source_entity_id)
                        LEFT JOIN content_relevance r ON r.content_id=c.content_id
                        WHERE c.platform IN ({placeholders}) AND c.published_at>=?
                        ORDER BY COALESCE(r.score,0) DESC,c.published_at DESC LIMIT ?""",
                    (*platforms, cutoff, limit)).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["teams"] = [dict(inner) for inner in connection.execute(
                        """SELECT t.team_id,t.school,t.color,t.logos_json FROM content_teams ct
                           JOIN teams t USING(team_id)
                           WHERE ct.content_id=? AND ct.confidence>=0.75
                           ORDER BY ct.confidence DESC LIMIT 3""", (item["content_id"],))]
                    for team in item["teams"]:
                        logos = json.loads(team.pop("logos_json") or "[]")
                        team["logo"] = logos[0] if logos else None
                        team["accent"] = readable_accent(team.get("color"))
                    item["headline"] = display_text(item, limit=110)
                    item["published_label"] = display_timestamp(item.get("published_at"))
                    items.append(item)
                total = connection.execute(
                    f"SELECT COUNT(*) FROM content_items WHERE platform IN ({placeholders})",
                    platforms).fetchone()[0]
                streams.append({"key": key, "label": label, "description": description,
                                "total": total, "items": items})
        return streams

    def top_developments(self, limit: int = 20, *, min_score: float = 0.0,
                         days: int = 7) -> list[dict[str, Any]]:
        """Highest-relevance recent content across every platform.

        This is the answer to "what actually happened" rather than "what was
        posted most recently": ordering comes from the stored relevance score,
        and each row carries the factors that produced it.
        """
        self.initialize()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT c.content_id,c.platform,c.title,c.body_text,c.canonical_url,
                   c.original_url,c.publisher_name,c.author_name,c.published_at,
                   c.content_type,c.source_role,e.name source_entity_name,
                   r.score,r.topic,r.factors_json
                   FROM content_items c JOIN content_relevance r USING(content_id)
                   LEFT JOIN source_entities e USING(source_entity_id)
                   WHERE c.published_at>=? AND r.score>=?
                   ORDER BY r.score DESC,c.published_at DESC LIMIT ?""",
                (cutoff, min_score, limit)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["factors"] = json.loads(item.pop("factors_json") or "[]")
                item["headline"] = display_text(item, limit=140)
                item["published_label"] = display_timestamp(item.get("published_at"))
                item["teams"] = [dict(inner) for inner in connection.execute(
                    """SELECT t.team_id,t.school,t.conference,t.color,ct.confidence
                       FROM content_teams ct JOIN teams t USING(team_id)
                       WHERE ct.content_id=? ORDER BY ct.confidence DESC""",
                    (item["content_id"],))]
                for team in item["teams"]:
                    team["accent"] = readable_accent(team.get("color"))
                item["conferences"] = [inner[0] for inner in connection.execute(
                    "SELECT conference FROM content_conferences WHERE content_id=? ORDER BY confidence DESC",
                    (item["content_id"],))]
                item["players"] = [dict(inner) for inner in connection.execute(
                    """SELECT cp.player_id,cp.confidence,cp.method,
                       p.first_name||' '||p.last_name name,p.position,p.team
                       FROM content_players cp
                       LEFT JOIN players p ON p.player_id=cp.player_id AND p.season=cp.season
                       WHERE cp.content_id=? ORDER BY cp.confidence DESC""",
                    (item["content_id"],))]
                item["games"] = [dict(inner) for inner in connection.execute(
                    """SELECT cg.game_id,cg.game_match_score,cg.method,
                       g.home_team,g.away_team,g.week
                       FROM content_games cg LEFT JOIN games g USING(game_id)
                       WHERE cg.content_id=? ORDER BY cg.game_match_score DESC""",
                    (item["content_id"],))]
                role_row = connection.execute(
                    "SELECT role,confidence,evidence_json FROM content_roles WHERE content_id=?",
                    (item["content_id"],)).fetchone()
                item["role_evidence"] = (json.loads(role_row["evidence_json"])
                                         if role_row else [])
                item["role_confidence"] = role_row["confidence"] if role_row else None
                item["tag_evidence"] = [dict(inner) for inner in connection.execute(
                    """SELECT kind,target,matched_text,location,method,confidence
                       FROM content_tag_evidence WHERE content_id=?
                       ORDER BY confidence DESC""", (item["content_id"],))]
                # Attach the school name to team evidence so the page can say
                # "matched 'buckeyes' in the lead" rather than a bare team id.
                schools = {str(team["team_id"]): team["school"] for team in item["teams"]}
                for evidence in item["tag_evidence"]:
                    if evidence["kind"] == "team":
                        evidence["label"] = schools.get(evidence["target"], evidence["target"])
                    else:
                        evidence["label"] = evidence["target"]
                items.append(item)
        return items

    def for_game(self, game_id: int, team_ids: tuple[int, int], limit: int = 30) -> dict[str,list[dict]]:
        self.initialize(); cutoff=(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()
        with closing(self._connect()) as connection:
            rows=connection.execute(
                """SELECT DISTINCT c.*,e.name source_entity_name,
                   COALESCE(cg.game_match_score,0.45) relevance,
                   COALESCE(r.score,0) relevance_score,r.factors_json
                   FROM content_items c LEFT JOIN source_entities e USING(source_entity_id)
                   LEFT JOIN content_relevance r ON r.content_id=c.content_id
                   LEFT JOIN content_games cg ON cg.content_id=c.content_id AND cg.game_id=?
                   LEFT JOIN content_teams ct ON ct.content_id=c.content_id
                   WHERE c.published_at>=? AND (cg.game_match_score>=0.75 OR ct.team_id IN (?,?))
                   ORDER BY relevance DESC,relevance_score DESC,c.published_at DESC LIMIT ?""",
                (game_id,cutoff,team_ids[0],team_ids[1],limit)).fetchall(); items=[]
            for row in rows:
                item=dict(row); content_id=item["content_id"]; item.pop("raw_json",None)
                item["topics"]=[r[0] for r in connection.execute(
                    "SELECT topic FROM content_topics WHERE content_id=? ORDER BY topic",(content_id,))]
                item["relevance_label"]="Game-linked" if item["relevance"]>=0.75 else "Team context"
                item["headline"]=display_text(item)
                item["published_label"]=display_timestamp(item.get("published_at"))
                item["relevance_factors"]=json.loads(item.pop("factors_json") or "[]")
                items.append(item)
        layers={"reported":[],"official":[],"analyzed":[],"scouted":[],"watched":[],"discussed":[],"other":[]}
        for item in items:
            topics=set(item["topics"]); role=item["source_role"]
            if topics & {"NFL_DRAFT","RECRUITING"}: layer="scouted"
            elif role=="OFFICIAL_CONFIRMATION": layer="official"
            elif role=="REPORTING_UNDETERMINED": layer="reported"
            elif role=="ANALYSIS": layer="analyzed"
            elif role=="COMMUNITY_REACTION": layer="discussed"
            else: layer="other"
            layers[layer].append(item)
        return layers
