"""Cross-entity search over teams, players, games, and reporting.

One box, four kinds of answer. A query is matched against each entity type with
rules appropriate to it -- team aliases, person names with initials collapsed,
matchup text, and story headlines -- and results carry why they matched so an
unexpected hit is explainable rather than mysterious.

Ranking is by match strength first and prominence second, because a search for
"Smith" should lead with the players who are actually written about.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from sports_aggregator.cfb.identity import readable_accent
from sports_aggregator.cfb.models import normalize_alias, normalize_person_name
from sports_aggregator.cfb.repository import CFBRepository, _logo_pair


MIN_QUERY = 2


def _score(candidate: str, query: str) -> tuple[float, str] | None:
    """Match strength for one candidate string, with a reason."""
    if not candidate:
        return None
    if candidate == query:
        return 1.0, "exact match"
    if candidate.startswith(query):
        return 0.85, "starts with the query"
    if f" {query}" in f" {candidate}":
        return 0.7, "word match"
    if query in candidate:
        return 0.5, "contains the query"
    # Abbreviated forms people actually type: "sac state" for Sacramento State,
    # "ok state" for Oklahoma State. Every query token must prefix a candidate
    # token, in order, so "state" alone cannot match every school.
    query_tokens, candidate_tokens = query.split(), candidate.split()
    if len(query_tokens) > 1 and len(query_tokens) <= len(candidate_tokens):
        position = 0
        for token in query_tokens:
            while position < len(candidate_tokens) and not candidate_tokens[position].startswith(token):
                position += 1
            if position == len(candidate_tokens):
                break
            position += 1
        else:
            return 0.45, "abbreviated word match"
    return None


def search(repository: CFBRepository, query: str, *, season: int,
           limit: int = 8) -> dict[str, Any]:
    """Search every entity type at once."""
    raw = (query or "").strip()
    if len(raw) < MIN_QUERY:
        return {"query": raw, "too_short": True, "teams": [], "players": [],
                "games": [], "stories": [], "total": 0}
    alias_query = normalize_alias(raw)
    person_query = normalize_person_name(raw)
    repository.initialize()
    with closing(repository._connect()) as connection:
        teams = _search_teams(connection, alias_query, season, limit)
        players = _search_players(connection, person_query, season, limit)
        games = _search_games(connection, alias_query, season, limit)
        stories = _search_stories(connection, raw, limit)
    total = len(teams) + len(players) + len(games) + len(stories)
    return {"query": raw, "too_short": False, "season": season,
            "teams": teams, "players": players, "games": games,
            "stories": stories, "total": total}


def _search_teams(connection, query: str, season: int, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT t.team_id,t.school,t.mascot,t.conference,t.color,t.logos_json,
           a.normalized_alias
           FROM teams t JOIN team_aliases a ON a.team_id=t.team_id""").fetchall()
    best: dict[int, dict[str, Any]] = {}
    for row in rows:
        scored = _score(row["normalized_alias"], query)
        if not scored:
            continue
        strength, reason = scored
        current = best.get(row["team_id"])
        if current and current["score"] >= strength:
            continue
        import json
        logos = json.loads(row["logos_json"] or "[]")
        best[row["team_id"]] = {
            "team_id": row["team_id"], "school": row["school"],
            "mascot": row["mascot"], "conference": row["conference"],
            "logo": _logo_pair(logos)[0],
            "logo_dark": _logo_pair(logos)[1],
            "accent": readable_accent(row["color"]),
            "score": strength,
            "reason": f"{reason} on alias '{row['normalized_alias']}'",
        }
    return sorted(best.values(), key=lambda item: (-item["score"], item["school"]))[:limit]


def _search_players(connection, query: str, season: int, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT p.player_id,p.first_name,p.last_name,p.team,p.position,p.jersey,
           p.season,t.team_id,t.color,t.logos_json
           FROM players p LEFT JOIN teams t ON t.school=p.team
           WHERE p.season=?""", (season,)).fetchall()
    results = []
    for row in rows:
        name = f"{row['first_name']} {row['last_name']}"
        scored = _score(normalize_person_name(name), query)
        if not scored:
            continue
        strength, reason = scored
        import json
        logos = json.loads(row["logos_json"] or "[]")
        results.append({
            "player_id": row["player_id"], "name": name, "team": row["team"],
            "team_id": row["team_id"], "position": row["position"],
            "jersey": row["jersey"], "season": row["season"],
            "logo": _logo_pair(logos)[0],
            "logo_dark": _logo_pair(logos)[1],
            "accent": readable_accent(row["color"]),
            "score": strength, "reason": reason,
        })
    if not results:
        return []
    # Prominence breaks ties: a searched name that sources actually write about
    # should lead, rather than whichever roster row was read first.
    identifiers = [row["player_id"] for row in results]
    placeholders = ",".join("?" for _ in identifiers)
    # Prominence is a nicety; a store without the reporting schema must still
    # return roster results rather than failing the whole search.
    try:
        mentions = dict(connection.execute(
            f"""SELECT player_id,COUNT(*) FROM content_players
                WHERE player_id IN ({placeholders}) GROUP BY 1""", identifiers).fetchall())
    except Exception:
        mentions = {}
    stats = dict(connection.execute(
        f"""SELECT player_id,COUNT(*) FROM player_season_stats
            WHERE player_id IN ({placeholders}) GROUP BY 1""", identifiers).fetchall())
    for row in results:
        row["mentions"] = mentions.get(row["player_id"], 0)
        row["stat_rows"] = stats.get(row["player_id"], 0)
    results.sort(key=lambda item: (-item["score"], -item["mentions"],
                                   -item["stat_rows"], item["name"]))
    return results[:limit]


def _search_games(connection, query: str, season: int, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT game_id,week,season,start_date,home_team,away_team,
           home_team_id,away_team_id,venue,completed,home_points,away_points
           FROM games WHERE season=? ORDER BY start_date""", (season,)).fetchall()
    results = []
    for row in rows:
        home = _score(normalize_alias(row["home_team"]), query)
        away = _score(normalize_alias(row["away_team"]), query)
        best = max((item for item in (home, away) if item), key=lambda item: item[0], default=None)
        if not best:
            continue
        matched = row["home_team"] if home and (not away or home[0] >= away[0]) else row["away_team"]
        results.append({
            "game_id": row["game_id"], "week": row["week"], "season": row["season"],
            "start_date": row["start_date"], "home_team": row["home_team"],
            "away_team": row["away_team"], "venue": row["venue"],
            "completed": bool(row["completed"]),
            "score": best[0], "reason": f"{best[1]} on {matched}",
        })
    results.sort(key=lambda item: (-item["score"], item["start_date"]))
    return results[:limit]


def _search_stories(connection, query: str, limit: int) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    try:
        rows = connection.execute(
            """SELECT c.content_id,c.title,c.body_text,c.canonical_url,c.platform,
               c.published_at,c.source_role,e.name source_name,
               COALESCE(r.score,0) relevance
               FROM content_items c
               JOIN content_sport_decisions sd USING(content_id)
               LEFT JOIN source_entities e USING(source_entity_id)
               LEFT JOIN content_relevance r ON r.content_id=c.content_id
               WHERE sd.eligible=1 AND (c.title LIKE ? OR c.body_text LIKE ?)
               ORDER BY COALESCE(r.score,0) DESC,c.published_at DESC LIMIT ?""",
            (pattern, pattern, limit)).fetchall()
    except Exception:
        return []
    from sports_aggregator.social.content import display_text, display_timestamp, label_linked_piece
    results = []
    for row in rows:
        item = dict(row)
        item["headline"] = display_text(item, limit=120)
        item["published_label"] = display_timestamp(item.get("published_at"))
        item.pop("body_text", None)
        item["reason"] = "text contains the query"
        results.append(label_linked_piece(item))
    return results
