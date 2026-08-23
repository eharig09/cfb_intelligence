from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests
from dateutil import parser as dateparser

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

TEAM_ALIASES = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "GNB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "JAC": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "KAN": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders",
    "LVR": "Las Vegas Raiders",
    "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NWE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NOR": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers",
    "SFO": "San Francisco 49ers",
    "SEA": "Seattle Seahawks",
    "TB": "Tampa Bay Buccaneers",
    "TAM": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}


class TTLCache:
    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)


news_cache = TTLCache(default_ttl=300)


def get_current_draft_year(now: Optional[datetime] = None) -> int:
    now = now or datetime.now()
    return now.year if now.month <= 7 else now.year + 1


def normalize_team(team: Optional[str]) -> Optional[str]:
    if not team:
        return None
    cleaned = re.sub(r"\s+", " ", team.strip())
    if not cleaned:
        return None
    upper = cleaned.upper()
    return TEAM_ALIASES.get(upper, cleaned)


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _query_tokens(query: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-z0-9']+", _normalize_text(query)) if tok]


def _cache_key(
    source: str,
    query: str,
    draft_year: Optional[int],
    team: Optional[str],
    terms: Optional[list[str]] = None,
) -> str:
    year_part = str(draft_year) if draft_year else "any"
    team_part = _normalize_text(team or "") or "anyteam"
    terms_part = "|".join(sorted(_normalize_text(term) for term in (terms or []) if term.strip())) or "noterms"
    return f"{source}:{year_part}:{team_part}:{terms_part}:{_normalize_text(query)}"


def _request_text(url: str, timeout: int = 20, retries: int = 3, backoff: float = 1.5) -> Optional[str]:
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            if resp.status_code in (403, 429):
                print(f"[_request_text] HTTP {resp.status_code} — {url}", file=sys.stderr)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_err = exc
            time.sleep((backoff ** attempt) + random.random() * 0.25)
    print(f"[_request_text] FAILED {url} after {retries} tries: {last_err}", file=sys.stderr)
    return None


def parse_timestamp(timestamp_str: Optional[str]) -> Optional[datetime]:
    if not timestamp_str:
        return None
    try:
        return dateparser.parse(timestamp_str)
    except Exception:
        return None


def sort_articles(articles: list[dict]) -> list[dict]:
    def _parse(date_str: str) -> datetime:
        if not date_str:
            return datetime.min.replace(tzinfo=timezone.utc)
        dt = parse_timestamp(str(date_str).strip())
        if dt is not None:
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        lower = str(date_str).strip().lower()
        m = re.match(
            r"^\s*(\d+)\s*(d|day|days|h|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\s*$",
            lower,
        )
        if m:
            value, unit = int(m.group(1)), m.group(2)
            now = datetime.now(timezone.utc)
            if unit.startswith("d"):
                return now - timedelta(days=value)
            if unit.startswith("h"):
                return now - timedelta(hours=value)
            if unit.startswith("m"):
                return now - timedelta(minutes=value)
            return now - timedelta(seconds=value)
        return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(articles, key=lambda x: _parse(x.get("date", "")), reverse=True)


def _dedupe_articles(articles: Iterable[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for article in articles:
        url = (article.get("url") or "").strip()
        title = (article.get("title") or "").strip().lower()
        key = url or title
        if key:
            deduped[key] = article
    return list(deduped.values())


def _name_variants(name: str) -> list[str]:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return []
    parts = name.split()
    variants = {name}
    if len(parts) >= 2:
        variants.add(f"{parts[0]} {parts[-1]}")
        variants.add(parts[-1])
    return [v for v in variants if v]


def _matches_query(article: dict, query: str) -> bool:
    haystack = _normalize_text(
        " ".join(
            [
                article.get("title", ""),
                article.get("summary", ""),
                article.get("author", ""),
                article.get("source", ""),
                article.get("url", ""),
            ]
        )
    )

    variants = [_normalize_text(v) for v in _name_variants(query)]
    if any(v and v in haystack for v in variants):
        return True

    tokens = [t for t in _query_tokens(query) if len(t) > 2]
    if len(tokens) >= 2:
        return tokens[0] in haystack and tokens[-1] in haystack
    return bool(tokens) and all(token in haystack for token in tokens)


def _score_article_for_team(article: dict, team: Optional[str]) -> int:
    if not team:
        return 0
    haystack = _normalize_text(
        " ".join(
            [
                article.get("title", ""),
                article.get("summary", ""),
                article.get("author", ""),
                article.get("source", ""),
                article.get("url", ""),
            ]
        )
    )
    team_norm = _normalize_text(team)
    score = 0
    if team_norm in haystack:
        score += 3
    for token in [t for t in _query_tokens(team) if len(t) > 2]:
        if token in haystack:
            score += 1
    return score


def normalize_terms(terms: Optional[list[str]]) -> list[str]:
    if not terms:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms:
        t = re.sub(r"\s+", " ", (term or "").strip())
        key = _normalize_text(t)
        if t and key not in seen:
            cleaned.append(t)
            seen.add(key)
    return cleaned


def _score_article_for_terms(article: dict, terms: Optional[list[str]]) -> int:
    terms = normalize_terms(terms)
    if not terms:
        return 0
    haystack = _normalize_text(
        " ".join(
            [
                article.get("title", ""),
                article.get("summary", ""),
                article.get("author", ""),
                article.get("source", ""),
                article.get("url", ""),
            ]
        )
    )
    score = 0
    for term in terms:
        term_norm = _normalize_text(term)
        if term_norm and term_norm in haystack:
            score += 3
            continue
        tokens = [tok for tok in _query_tokens(term) if len(tok) > 2]
        if tokens and all(tok in haystack for tok in tokens):
            score += 2
    return score


def _filter_articles(
    news: list[dict],
    query: str,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    filtered = [article for article in news if _matches_query(article, query)]
    if team:
        team_matches = [article for article in filtered if _score_article_for_team(article, team) > 0]
        filtered = team_matches or filtered

    if terms:
        term_matches = [article for article in filtered if _score_article_for_terms(article, terms) > 0]
        if strict_terms:
            return term_matches
        filtered = term_matches or filtered

    return filtered


def _google_news_rss_url(search_query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(search_query)}&hl=en-US&gl=US&ceid=US:en"


def _parse_google_news_rss(text: str, default_source_name: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()
        source = default_source_name
        source_tag = item.find("source")
        if source_tag is not None and (source_tag.text or "").strip():
            source = source_tag.text.strip()
        items.append(
            {
                "title": title,
                "url": link,
                "author": source,
                "source": source,
                "date": pub_date,
                "summary": description,
            }
        )
    return items


def _run_google_news_query(search_query: str, source_name: str) -> list[dict]:
    text = _request_text(_google_news_rss_url(search_query))
    if not text:
        return []
    return _parse_google_news_rss(text, source_name)


def _scrape_multi_query_google_news(
    *,
    source_name: str,
    prospect_name: str,
    queries: list[str],
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    cache_key = _cache_key(source_name, prospect_name, draft_year, team, terms)
    cached = news_cache.get(cache_key)
    if cached is not None:
        return cached

    articles: list[dict] = []
    for q in queries:
        try:
            articles.extend(_run_google_news_query(q, source_name))
        except Exception as exc:
            print(f"[{source_name}] query failed: {q} :: {exc}", file=sys.stderr)

    articles = _filter_articles(
        _dedupe_articles(articles),
        prospect_name,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )
    articles = sort_articles(articles)
    if team or terms:
        articles = sorted(
            articles,
            key=lambda article: (
                _score_article_for_team(article, team),
                _score_article_for_terms(article, terms),
                parse_timestamp(article.get("date", "")) or datetime.min,
            ),
            reverse=True,
        )
    news_cache.set(cache_key, articles)
    return articles


def _base_queries(
    player_name: str,
    draft_year: Optional[int],
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
) -> list[str]:
    queries: list[str] = []
    team = normalize_team(team)
    terms = normalize_terms(terms)
    tail = " ".join(f'"{term}"' for term in terms).strip()

    if draft_year and team:
        queries.append(f'"{player_name}" "{team}" "{draft_year} NFL Draft" {tail}'.strip())
    if draft_year:
        queries.append(f'"{player_name}" "{draft_year} NFL Draft" {tail}'.strip())
    if team:
        queries.extend(
            [
                f'"{player_name}" "{team}" "NFL Draft" {tail}'.strip(),
                f'"{player_name}" "{team}" scouting report {tail}'.strip(),
                f'"{player_name}" "{team}" mock draft {tail}'.strip(),
            ]
        )
    queries.extend(
        [
            f'"{player_name}" "NFL Draft" {tail}'.strip(),
            f'"{player_name}" scouting report {tail}'.strip(),
            f'"{player_name}" profile {tail}'.strip(),
            f'"{player_name}" big board {tail}'.strip(),
            f'"{player_name}" mock draft {tail}'.strip(),
            f'"{player_name}" football {tail}'.strip(),
            f'"{player_name}" {tail}'.strip(),
        ]
    )
    return list(dict.fromkeys(q for q in queries if q))


def _domain_queries(
    domain: str,
    prospect_name: str,
    draft_year: Optional[int],
    team: Optional[str] = None,
    extra_terms: Optional[list[str]] = None,
    custom_terms: Optional[list[str]] = None,
) -> list[str]:
    extra_terms = extra_terms or []
    custom_terms = normalize_terms(custom_terms)
    tail = " ".join(extra_terms + [f'"{term}"' for term in custom_terms]).strip()
    team = normalize_team(team)
    queries: list[str] = []
    if draft_year and team:
        queries.append(f'site:{domain} "{prospect_name}" "{team}" "{draft_year} NFL Draft" {tail}'.strip())
    if draft_year:
        queries.append(f'site:{domain} "{prospect_name}" "{draft_year} NFL Draft" {tail}'.strip())
    if team:
        queries.extend(
            [
                f'site:{domain} "{prospect_name}" "{team}" "NFL Draft" {tail}'.strip(),
                f'site:{domain} "{prospect_name}" "{team}" mock draft {tail}'.strip(),
                f'site:{domain} "{prospect_name}" "{team}" scouting report {tail}'.strip(),
            ]
        )
    queries.extend(
        [
            f'site:{domain} "{prospect_name}" "NFL Draft" {tail}'.strip(),
            f'site:{domain} "{prospect_name}" scouting report {tail}'.strip(),
            f'site:{domain} "{prospect_name}" profile {tail}'.strip(),
            f'site:{domain} "{prospect_name}" big board {tail}'.strip(),
            f'site:{domain} "{prospect_name}" mock draft {tail}'.strip(),
            f'site:{domain} "{prospect_name}" football {tail}'.strip(),
            f'site:{domain} "{prospect_name}" {tail}'.strip(),
        ]
    )
    return queries


def scrape_pff(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    queries = _domain_queries(
        "pff.com",
        prospect_name,
        draft_year,
        team=team,
        extra_terms=['prospect', 'profile', 'scouting', 'rankings', '"PFF"'],
        custom_terms=terms,
    )
    queries += [
        f'site:pff.com "{prospect_name}" "NFL Draft profile"',
        f'site:pff.com "{prospect_name}" "draft guide"',
        f'site:pff.com "{prospect_name}"',
    ]
    return _scrape_multi_query_google_news(
        source_name="PFF",
        prospect_name=prospect_name,
        queries=queries,
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_nfl(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="NFL.com",
        prospect_name=prospect_name,
        queries=_domain_queries("nfl.com", prospect_name, draft_year, team=team, extra_terms=["prospects", '"NFL Draft"'], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_espn(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="ESPN",
        prospect_name=prospect_name,
        queries=_domain_queries("espn.com", prospect_name, draft_year, team=team, extra_terms=['"NFL Draft"', "scouting"], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_cbs(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="CBS Sports",
        prospect_name=prospect_name,
        queries=_domain_queries("cbssports.com", prospect_name, draft_year, team=team, extra_terms=['"NFL Draft"', "prospect"], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_fox(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="Fox Sports",
        prospect_name=prospect_name,
        queries=_domain_queries("foxsports.com", prospect_name, draft_year, team=team, extra_terms=['"NFL Draft"', "prospect"], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_athletic(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="The Athletic",
        prospect_name=prospect_name,
        queries=_domain_queries("theathletic.com", prospect_name, draft_year, team=team, extra_terms=['"NFL Draft"', "scouting"], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_yahoo(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="Yahoo Sports",
        prospect_name=prospect_name,
        queries=_domain_queries("sports.yahoo.com", prospect_name, draft_year, team=team, extra_terms=['"NFL Draft"', "prospect"], custom_terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


def scrape_google_news(
    prospect_name: str,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
) -> list[dict]:
    return _scrape_multi_query_google_news(
        source_name="Google News",
        prospect_name=prospect_name,
        queries=_base_queries(prospect_name, draft_year, team=team, terms=terms),
        draft_year=draft_year,
        team=team,
        terms=terms,
        strict_terms=strict_terms,
    )


SOURCE_REGISTRY: dict[str, Callable[[str, Optional[int], Optional[str], Optional[list[str]], bool], list[dict]]] = {
    "pff": scrape_pff,
    "nfl": scrape_nfl,
    "espn": scrape_espn,
    "cbs": scrape_cbs,
    "fox": scrape_fox,
    "athletic": scrape_athletic,
    "yahoo": scrape_yahoo,
    "google": scrape_google_news,
}

DEFAULT_SOURCES = ["pff", "nfl", "espn", "cbs", "fox", "athletic", "yahoo", "google"]


def search_prospect_news(
    prospect_name: str,
    *,
    draft_year: Optional[int] = None,
    team: Optional[str] = None,
    terms: Optional[list[str]] = None,
    strict_terms: bool = False,
    sources: Optional[list[str]] = None,
    limit: int = 25,
    max_workers: Optional[int] = None,
) -> list[dict]:
    prospect_name = (prospect_name or "").strip()
    if not prospect_name:
        raise ValueError("prospect_name is required")

    draft_year = draft_year or get_current_draft_year()
    team = normalize_team(team)
    terms = normalize_terms(terms)
    sources = sources or DEFAULT_SOURCES
    invalid = [src for src in sources if src not in SOURCE_REGISTRY]
    if invalid:
        raise ValueError(f"Unknown source(s): {', '.join(invalid)}")

    articles: list[dict] = []
    workers = max_workers or min(len(sources), 8)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(SOURCE_REGISTRY[source], prospect_name, draft_year, team, terms, strict_terms): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                source_articles = future.result() or []
                for article in source_articles:
                    article.setdefault("source_key", source)
                    article.setdefault("draft_year", draft_year)
                    if team:
                        article.setdefault("team", team)
                        article["team_score"] = _score_article_for_team(article, team)
                    if terms:
                        article["term_score"] = _score_article_for_terms(article, terms)
                        article["terms"] = terms
                articles.extend(source_articles)
            except Exception as exc:
                print(f"[search_prospect_news] source '{source}' failed: {exc}", file=sys.stderr)

    articles = _dedupe_articles(articles)
    articles = sort_articles(articles)
    if team or terms:
        articles = sorted(
            articles,
            key=lambda a: (
                _score_article_for_team(a, team),
                _score_article_for_terms(a, terms),
                parse_timestamp(a.get("date", "")) or datetime.min,
            ),
            reverse=True,
        )
    if limit > 0:
        articles = articles[:limit]
    return articles


def _print_results(
    articles: list[dict],
    show_urls: bool = False,
    show_team_scores: bool = False,
    show_term_scores: bool = False,
) -> None:
    if not articles:
        print("No matching articles found.")
        return

    for idx, article in enumerate(articles, start=1):
        print(f"{idx:>2}. {article.get('title', 'No Title')}")
        print(f"    Source: {article.get('source', 'Unknown Source')}")
        print(f"    Date:   {article.get('date', 'Unknown Date')}")
        if show_team_scores and article.get("team"):
            print(f"    Team match score: {article.get('team_score', 0)}")
        if show_term_scores and article.get("terms"):
            print(f"    Term match score: {article.get('term_score', 0)}")
        if show_urls:
            print(f"    URL:    {article.get('url', '')}")
        print()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search NFL draft prospect coverage across public sources. Defaults to the active draft cycle year."
    )
    parser.add_argument("prospect_name", help="Prospect name to search for, e.g. 'Abdul Carter'")
    parser.add_argument(
        "--draft-year",
        type=int,
        default=get_current_draft_year(),
        help="Draft cycle year to target. Defaults to the active cycle based on today's date.",
    )
    parser.add_argument(
        "--team",
        help="Optional team filter/boost. Example: --team DAL or --team 'Dallas Cowboys'",
    )
    parser.add_argument(
        "--term",
        action="append",
        default=[],
        help="Optional custom term(s) to add, e.g. --term injury --term trade",
    )
    parser.add_argument(
        "--strict-terms",
        action="store_true",
        help="Require at least one custom term to appear in matched results.",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=sorted(SOURCE_REGISTRY.keys()),
        default=DEFAULT_SOURCES,
        help="Sources to search. Default searches all available sources.",
    )
    parser.add_argument("--limit", type=int, default=25, help="Maximum number of results to return")
    parser.add_argument("--json-out", help="Optional file path to save results as JSON")
    parser.add_argument("--show-urls", action="store_true", help="Print article URLs in terminal output")
    parser.add_argument(
        "--show-team-scores",
        action="store_true",
        help="Print the internal team match score for each result when using --team.",
    )
    parser.add_argument(
        "--show-term-scores",
        action="store_true",
        help="Print the internal custom-term match score for each result when using --term.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        normalized_team = normalize_team(args.team)
        normalized_terms = normalize_terms(args.term)
        articles = search_prospect_news(
            args.prospect_name,
            draft_year=args.draft_year,
            team=normalized_team,
            terms=normalized_terms,
            strict_terms=args.strict_terms,
            sources=args.sources,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Draft cycle: {args.draft_year}")
    if args.team:
        print(f"Team tag:    {normalize_team(args.team)}")
    if args.term:
        print(f"Custom term: {', '.join(normalize_terms(args.term))}")
        if args.strict_terms:
            print("Term mode:   strict")
    print()

    _print_results(
        articles,
        show_urls=args.show_urls,
        show_team_scores=args.show_team_scores,
        show_term_scores=args.show_term_scores,
    )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(articles, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(articles)} result(s) to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
