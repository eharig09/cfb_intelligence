"""Research and normalize local college-football reporting sources.

The discovery pass uses a broad, team-specific Google News RSS query to find
publishers that are actively covering a program. A publisher is retained only
after a second, domain-constrained query returns coverage for that same team.
This makes the generated registry reproducible and keeps guessed outlet names or
RSS URLs out of production data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
import unicodedata
from urllib.parse import quote_plus, urlsplit
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup


GOOGLE_NEWS = "https://news.google.com/rss/search"
USER_AGENT = "sports-news-aggregator/1.0 local-source-research"

# These sources remain useful in the national stream, but they are not evidence
# that a team has the local reporting requested by this registry.
NATIONAL_DOMAINS = {
    "247sports.com", "actionnetwork.com", "apnews.com", "athlonsports.com",
    "atozsports.com", "bleachernation.com", "bleacherreport.com", "brobible.com",
    "cbssports.com", "cbsnews.com", "collegefootballnews.com", "espn.com",
    "facebook.com", "fbschedules.com", "foxnews.com", "foxsports.com",
    "herosports.com", "larrybrownsports.com", "legacy.com", "maxpreps.com", "msn.com",
    "newsbreak.com", "ncaa.com", "nypost.com", "nytimes.com", "on3.com",
    "patch.com", "rivals.com", "rotoballer.com", "roundtable.io", "si.com",
    "sling.com", "sports.betmgm.com", "sportsbusinessjournal.com",
    "sportingnews.com", "sports.yahoo.com", "styleblueprint.com", "usatoday.com",
    "washingtonpost.com", "yardbarker.com",
}
FAN_OR_OFFICIAL_DOMAINS = {
    "azdesertswarm.com", "bamahammer.com", "bcinterruption.com",
    "12thman.com", "bringonthecats.com", "burntorangenation.com", "caneswarning.com",
    "cardchronicle.com", "cincyontheprowl.com", "dawnofthedawg.com",
    "fromtherumbleseat.com", "gbmwolverine.com", "gigemgazette.com",
    "goodbullhunting.com",
    "goaztecs.com", "gobison.com", "gojoebruin.com", "gostanford.com",
    "hailwv.com", "insidenu.com", "insidetheloudhouse.com", "kentstatesports.com",
    "kuathletics.com", "libertyflames.com", "missouristatebears.com", "mutigers.com",
    "owlsports.com", "razorbackers.com", "sjsuspartans.com", "smokingmusket.com",
    "spartanavenue.com", "stateoftheu.com", "throughthephog.com", "tomahawknation.com",
    "underdogdynasty.com", "uwdawgpound.com", "virginiasports.com",
    "widerightnattylite.com",
}
IRRELEVANT_DOMAINS = {
    "charlottefootballclub.com", "diocesisdesalamanca.com", "mshale.com",
    "sekbernews.id", "steelernation.com",
}
MULTI_MARKET_DOMAINS = {
    "spectrumlocalnews.com", "spectrumnews1.com", "mynews13.com", "baynews9.com",
}
NATIONAL_NAMES = re.compile(
    r"\b(?:247sports|associated press|athlons?|bleacher report|cbs sports|espn|"
    r"fox sports|msn|ncaa|on3|rivals|sports illustrated|sporting news|"
    r"usa today|yahoo sports|yardbarker)\b", re.I,
)
LOCAL_NAME_HINTS = re.compile(
    r"\b(?:advocate|banner|chronicle|dispatch|examiner|gazette|herald|journal|"
    r"ledger|local|news|observer|paper|post|press|record|register|sentinel|star|"
    r"sun|telegram|telegraph|times|tribune|tv|weekly)\b", re.I,
)
TV_DOMAIN = re.compile(r"(?:^|\.)(?:w|k)[a-z]{2,4}(?:tv)?\.(?:com|net)$", re.I)
OFFICIAL_NAME = re.compile(r"\b(?:athletics|official athletic|university athletics)\b", re.I)

AMBIGUOUS_ALIASES = {
    "Miami": ("Miami Hurricanes football", "Miami FL Hurricanes football"),
    "Miami (OH)": ("Miami RedHawks football", "Miami Ohio football"),
    "USC": ("USC football", "USC Trojans football", "Southern California Trojans football"),
    "UTSA": ("UTSA Roadrunners football", "Texas San Antonio football"),
    "UMass": ("UMass Minutemen football", "Massachusetts Minutemen football"),
}
FOOTBALL_HEADLINE = re.compile(
    r"\b(?:football|quarterback|\bqb\b|running back|receiver|tight end|offensive|"
    r"defensive|linebacker|cornerback|coach|coordinator|roster|depth chart|practice|"
    r"camp|transfer|portal|recruit|commit|injur|suspend|kickoff|touchdown|game|"
    r"season|bowl|playoff|nfl|draft)\w*\b", re.I,
)
OTHER_SPORTS = re.compile(
    r"\b(?:men(?:'s)?|women(?:'s)?)?\s*(?:basketball|baseball|softball|soccer|"
    r"volleyball|hockey|lacrosse|golf|tennis|wrestling|gymnastics|swimming|"
    r"diving|rowing)|\b(?:track and field|cross country|hoops|hardwood|diamond)\b",
    re.I,
)
STRONG_FOOTBALL_HEADLINE = re.compile(
    r"\b(?:college football|football|gridiron|quarterback|\bqb\b|running back|"
    r"wide receiver|tight end|offensive line|defensive line|offensive tackle|"
    r"defensive tackle|defensive end|linebacker|cornerback|nickelback|free safety|"
    r"strong safety|touchdown|kickoff|field goal|punter|punt return|pass rush|"
    r"spring football|fall camp|depth chart|bowl game|college football playoff|"
    r"\bcfp\b|\bnfl\b)\w*\b",
    re.I,
)


def is_clearly_other_sport(text: str) -> bool:
    """Identify a named non-football sport without explicit football evidence."""
    return bool(OTHER_SPORTS.search(text) and not STRONG_FOOTBALL_HEADLINE.search(text))


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return host


def _publisher_id(domain: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", domain.casefold()).strip("-")
    return slug or hashlib.sha256(domain.encode()).hexdigest()[:12]


def google_news_url(query: str) -> str:
    return (f"{GOOGLE_NEWS}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en")


def team_aliases(team: str, mascot: str | None) -> list[str]:
    aliases = list(AMBIGUOUS_ALIASES.get(team, ()))
    aliases.append(f"{team} football")
    if mascot:
        aliases.append(f"{team} {mascot} football")
    return list(dict.fromkeys(alias for alias in aliases if len(alias) >= 8))


def _fetch_feed(query: str, *, timeout: float = 20) -> list[dict[str, Any]]:
    response = requests.get(
        google_news_url(query), timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"invalid Google News RSS for {query!r}: {parsed.bozo_exception}")
    results = []
    for entry in parsed.entries:
        source = entry.get("source") or {}
        source_url = str(source.get("href") or "")
        domain = _domain(source_url)
        if not domain:
            continue
        results.append({
            "publisher": str(source.get("title") or domain).strip(),
            "domain": domain,
            "publisher_url": source_url,
            "headline": str(entry.get("title") or "").strip(),
            "article_url": str(entry.get("link") or ""),
            "published": str(entry.get("published") or ""),
        })
    return results


def _is_local_candidate(item: dict[str, Any]) -> bool:
    domain = item["domain"]
    name = item["publisher"]
    if domain in NATIONAL_DOMAINS or any(domain.endswith("." + value)
                                         for value in NATIONAL_DOMAINS):
        return False
    if domain in FAN_OR_OFFICIAL_DOMAINS or domain in IRRELEVANT_DOMAINS:
        return False
    if NATIONAL_NAMES.search(name) or OFFICIAL_NAME.search(name) or domain.endswith(".edu"):
        return False
    return True


def _source_type(name: str, domain: str) -> str:
    if TV_DOMAIN.search(domain) or re.search(r"\b(?:tv|channel|local)\s*\d*\b", name, re.I):
        return "local_tv"
    if LOCAL_NAME_HINTS.search(name):
        return "newspaper"
    return "digital_local_news"


def _candidate_rank(item: dict[str, Any]) -> tuple[int, int, str]:
    local_hint = int(bool(LOCAL_NAME_HINTS.search(item["publisher"])
                          or TV_DOMAIN.search(item["domain"])))
    return (-local_hint, -item["broad_count"], item["publisher"].casefold())


def _search_text(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", folded.casefold()))


def _headline_mentions_team(headline: str, team: dict[str, Any], aliases: list[str]) -> bool:
    if is_clearly_other_sport(headline):
        return False
    if not FOOTBALL_HEADLINE.search(headline):
        return False
    text = f" {_search_text(headline)} "
    phrases = {_search_text(team.get("school")), _search_text(team.get("mascot"))}
    abbreviation = _search_text(team.get("abbreviation"))
    if len(abbreviation) >= 3:
        phrases.add(abbreviation)
    phrases.update(_search_text(alias).removesuffix(" football").strip() for alias in aliases)
    return any(phrase and f" {phrase} " in text for phrase in phrases)


def article_matches_team(text: str, team: dict[str, Any]) -> bool:
    """Apply the same team-and-football evidence floor during ingestion."""
    inventory_shape = {
        "school": team.get("team") or team.get("school"),
        "mascot": team.get("mascot"), "abbreviation": team.get("abbreviation"),
    }
    return _headline_mentions_team(text, inventory_shape, list(team.get("aliases") or ()))


def _team_inventory(database_path: str | Path, classification: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT t.team_id,t.school,t.mascot,t.abbreviation,t.conference,
                      t.classification,v.city,v.state
               FROM teams t LEFT JOIN venues v ON v.venue_id=t.venue_id
               WHERE t.classification=? ORDER BY t.school""", (classification,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def research_team(team: dict[str, Any], *, days: int = 365,
                  source_limit: int = 3, timeout: float = 20,
                  retain_pool: bool = False) -> dict[str, Any]:
    aliases = team_aliases(team["school"], team.get("mascot"))
    geography = " ".join(value for value in (team.get("city"), team.get("state")) if value)
    broad_query = f'"{aliases[0]}" {geography} when:{days}d'.strip()
    broad = _fetch_feed(broad_query, timeout=timeout)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in broad:
        if _is_local_candidate(item):
            grouped[item["domain"]].append(item)
    candidates = []
    for domain, evidence in grouped.items():
        representative = evidence[0]
        candidates.append({**representative, "broad_count": len(evidence),
                           "broad_evidence": evidence[:3]})
    candidates.sort(key=_candidate_rank)

    verified = []
    errors = []
    # Try extra candidates because some domain-constrained feeds will expose a
    # broad-query false positive and correctly fail verification.
    for candidate in candidates[: max(source_limit * 3, 8)]:
        fallback_query = f'"{aliases[0]}" site:{candidate["domain"]} when:{days}d'
        try:
            results = _fetch_feed(fallback_query, timeout=timeout)
        except Exception as exc:  # one publisher cannot invalidate the team
            errors.append(f"{candidate['domain']}: {exc}")
            continue
        matching = [item for item in results
                    if item["domain"] == candidate["domain"]
                    and _headline_mentions_team(item["headline"], team, aliases)]
        # One isolated mention does not establish that an outlet regularly
        # covers the program; it is commonly an opponent, wire story, or event
        # listing. Two recent domain-constrained results is the promotion floor.
        if len(matching) < 2:
            continue
        local_hint = bool(LOCAL_NAME_HINTS.search(candidate["publisher"])
                          or TV_DOMAIN.search(candidate["domain"]))
        evidence_count = len(matching)
        confidence = "high" if local_hint and evidence_count >= 3 else "medium"
        verified.append({
            "name": candidate["publisher"],
            "domain": candidate["domain"],
            "source_type": _source_type(candidate["publisher"], candidate["domain"]),
            "priority": 3,
            "team_specific_page": None,
            "native_rss": None,
            "sports_rss": None,
            "api_endpoint": None,
            "news_sitemap": None,
            "google_news_query": fallback_query,
            "google_news_rss": google_news_url(fallback_query),
            "paywall": None,
            "original_reporting": True if local_hint else None,
            "confidence": confidence,
            "notes": (f"Verified active coverage through {evidence_count} domain-constrained "
                      "Google News result(s); publisher-native endpoints remain unverified."),
            "verification": {
                "method": "GOOGLE_NEWS_DOMAIN_QUERY",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "result_count": evidence_count,
                "sample_headlines": [item["headline"] for item in matching[:3]],
            },
        })
    verified.sort(key=lambda source: (
        -int(source["source_type"] in {"newspaper", "local_tv"}),
        -source["verification"]["result_count"], source["name"].casefold(),
    ))
    if not retain_pool:
        verified = verified[:source_limit]
    for priority, source in enumerate(verified, start=1):
        source["priority"] = priority
    return {
        "team_id": team["team_id"], "team": team["school"],
        "conference": team.get("conference"), "division": team.get("classification"),
        "city": team.get("city"), "state": team.get("state"), "aliases": aliases,
        "sources": verified, "discovery_query": broad_query,
        "research_errors": errors,
    }


def _prune_geographic_outliers(results: dict[str, dict[str, Any]],
                               source_limit: int) -> list[dict[str, Any]]:
    """Remove weak opponent-market coverage using registry-wide evidence.

    A paper can mention an out-of-state opponent repeatedly without being part of
    that program's reporting ecosystem. If at least 60% of a publisher's verified
    evidence belongs to one state, weak mappings outside that state are removed.
    Multi-market local-news networks are exempt because their shared domain serves
    distinct local desks.
    """
    evidence: dict[str, Counter] = defaultdict(Counter)
    maximum: dict[tuple[str, str], int] = defaultdict(int)
    for team in results.values():
        state = team.get("state")
        if not state:
            continue
        for source in team["sources"]:
            count = int(source["verification"]["result_count"])
            evidence[source["domain"]][state] += count
            maximum[(source["domain"], state)] = max(
                maximum[(source["domain"], state)], count)
    dominant = {}
    for domain, states in evidence.items():
        state, count = states.most_common(1)[0]
        total = states.total()
        if len(states) > 1 and total and count / total >= 0.6:
            dominant[domain] = (state, maximum[(domain, state)])

    removed = []
    for team in results.values():
        kept = []
        for source in team["sources"]:
            home = dominant.get(source["domain"])
            count = int(source["verification"]["result_count"])
            is_outlier = (home and source["domain"] not in MULTI_MARKET_DOMAINS
                          and team.get("state") != home[0]
                          and count < home[1] * 0.5)
            if is_outlier:
                removed.append({
                    "team": team["team"], "team_state": team.get("state"),
                    "publisher": source["name"], "domain": source["domain"],
                    "publisher_dominant_state": home[0],
                    "team_result_count": count, "dominant_result_count": home[1],
                })
            else:
                kept.append(source)
        team["sources"] = kept[:source_limit]
        for priority, source in enumerate(team["sources"], start=1):
            source["priority"] = priority
    return removed


def research_registry(database_path: str | Path, *, classification: str = "fbs",
                      days: int = 365, source_limit: int = 3,
                      max_workers: int = 6, timeout: float = 20) -> dict[str, Any]:
    teams = _team_inventory(database_path, classification)
    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(research_team, team, days=days,
                               source_limit=source_limit, timeout=timeout,
                               retain_pool=True): team
                   for team in teams}
        for future in as_completed(futures):
            team = futures[future]
            try:
                results[team["school"]] = future.result()
            except Exception as exc:
                failures[team["school"]] = str(exc)
                results[team["school"]] = {
                    "team_id": team["team_id"], "team": team["school"],
                    "conference": team.get("conference"),
                    "division": team.get("classification"),
                    "city": team.get("city"), "state": team.get("state"),
                    "aliases": team_aliases(team["school"], team.get("mascot")),
                    "sources": [], "research_errors": [str(exc)],
                }
    removed = _prune_geographic_outliers(results, source_limit)
    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "classification": classification, "lookback_days": days,
            "verification_method": "broad discovery plus domain-constrained Google News RSS",
            "native_endpoint_policy": "null unless independently verified",
            "team_count": len(teams), "research_failures": failures,
            "geographic_outliers_removed": removed,
        },
        "teams": {name: results[name] for name in sorted(results)},
    }


def _inspect_publisher(domain: str, *, timeout: float = 15) -> dict[str, Any]:
    """Read only publisher-declared feed links and news sitemaps.

    Common guessed paths such as ``/feed`` are deliberately not tried. A feed is
    returned only when the publisher advertises it in HTML and it parses with at
    least one entry. A news sitemap must be declared in robots.txt and respond.
    """
    root = f"https://{domain}/"
    headers = {"User-Agent": USER_AGENT}
    declared: list[tuple[str, str]] = []
    errors = []
    try:
        response = requests.get(root, timeout=timeout, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.find_all("link", href=True):
            content_type = str(link.get("type") or "").casefold()
            rel = {str(value).casefold() for value in (link.get("rel") or [])}
            if "alternate" not in rel or content_type not in {
                "application/rss+xml", "application/atom+xml", "application/feed+json",
            }:
                continue
            declared.append((urljoin(response.url, str(link["href"])),
                             str(link.get("title") or "")))
    except Exception as exc:
        errors.append(f"homepage: {exc}")

    feeds = []
    for url, title in list(dict.fromkeys(declared))[:4]:
        try:
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if parsed.entries:
                feeds.append({"url": response.url, "title": title,
                              "entry_count": len(parsed.entries)})
        except Exception as exc:
            errors.append(f"feed {url}: {exc}")

    news_sitemaps = []
    try:
        robots = requests.get(urljoin(root, "/robots.txt"), timeout=timeout, headers=headers)
        robots.raise_for_status()
        for line in robots.text.splitlines():
            if not line.casefold().startswith("sitemap:"):
                continue
            url = line.split(":", 1)[1].strip()
            if "news" not in url.casefold():
                continue
            check = requests.get(url, timeout=timeout, headers=headers)
            if check.ok:
                news_sitemaps.append(check.url)
    except Exception as exc:
        errors.append(f"robots: {exc}")
    return {"feeds": feeds, "news_sitemaps": list(dict.fromkeys(news_sitemaps)),
            "errors": errors}


def enrich_machine_endpoints(registry: dict[str, Any], *, max_workers: int = 10,
                             timeout: float = 15) -> dict[str, Any]:
    domains = sorted({source["domain"] for team in registry["teams"].values()
                      for source in team["sources"]})
    findings: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(_inspect_publisher, domain, timeout=timeout): domain
                   for domain in domains}
        for future in as_completed(futures):
            domain = futures[future]
            try:
                findings[domain] = future.result()
            except Exception as exc:
                findings[domain] = {"feeds": [], "news_sitemaps": [],
                                    "errors": [str(exc)]}
    for team in registry["teams"].values():
        for source in team["sources"]:
            finding = findings[source["domain"]]
            sports = next((feed for feed in finding["feeds"]
                           if "sport" in f"{feed['title']} {feed['url']}".casefold()), None)
            general = next((feed for feed in finding["feeds"] if feed is not sports), None)
            source["sports_rss"] = sports["url"] if sports else None
            source["native_rss"] = general["url"] if general else None
            source["news_sitemap"] = (finding["news_sitemaps"][0]
                                      if finding["news_sitemaps"] else None)
            source["endpoint_verification"] = finding
    registry["metadata"]["publisher_endpoint_checks"] = len(domains)
    registry["metadata"]["publisher_endpoint_checked_at"] = datetime.now(timezone.utc).isoformat()
    return registry


def _publisher_registry(registry: dict[str, Any]) -> dict[str, Any]:
    publishers: dict[str, dict[str, Any]] = {}
    teams_by_publisher: dict[str, set[str]] = defaultdict(set)
    for team_name, team in registry["teams"].items():
        for source in team["sources"]:
            publisher_id = _publisher_id(source["domain"])
            teams_by_publisher[publisher_id].add(team_name)
            publishers.setdefault(publisher_id, {
                "publisher_id": publisher_id, "name": source["name"],
                "domain": source["domain"], "source_type": source["source_type"],
                "native_rss": source.get("native_rss"),
                "sports_rss": source.get("sports_rss"),
                "news_sitemap": source.get("news_sitemap"),
            })
    for publisher_id, publisher in publishers.items():
        publisher["teams"] = sorted(teams_by_publisher[publisher_id])
    return {key: publishers[key] for key in sorted(publishers)}


def coverage_report(registry: dict[str, Any]) -> dict[str, Any]:
    teams = list(registry["teams"].values())
    sources = [source for team in teams for source in team["sources"]]
    counts = Counter(len(team["sources"]) for team in teams)
    weak = [team["team"] for team in teams if len(team["sources"]) < 2]
    ambiguous = [team["team"] for team in teams if team["team"] in AMBIGUOUS_ALIASES]
    return {
        "total_teams_researched": len(teams),
        "teams_with_3_plus_sources": sum(count for size, count in counts.items() if size >= 3),
        "teams_with_2_sources": counts[2],
        "teams_with_only_1_source": counts[1],
        "teams_with_no_sources": counts[0],
        "teams_with_verified_native_rss": sum(any(
            source.get("native_rss") or source.get("sports_rss")
            for source in team["sources"]) for team in teams),
        "teams_without_verified_native_rss": sum(bool(team["sources"]) and not any(
            source.get("native_rss") or source.get("sports_rss")
            for source in team["sources"]) for team in teams),
        "teams_requiring_google_news_fallback": sum(bool(team["sources"]) for team in teams),
        "sources_with_original_reporting_signal": sum(
            source.get("original_reporting") is True for source in sources),
        "sources_with_unverified_original_reporting": sum(
            source.get("original_reporting") is None for source in sources),
        "sources_with_unverified_paywall_status": sum(
            source.get("paywall") is None for source in sources),
        "weak_coverage_teams": weak,
        "ambiguous_team_names": ambiguous,
        "research_failures": registry["metadata"].get("research_failures", {}),
    }


def write_deliverables(registry: dict[str, Any], output_dir: str | Path) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    publishers = _publisher_registry(registry)
    report = coverage_report(registry)
    endpoints = []
    problems = []
    for team in registry["teams"].values():
        issues = (["weak_local_coverage"] if len(team["sources"]) < 2 else [])
        if team["team"] in AMBIGUOUS_ALIASES:
            issues.append("ambiguous_team_name")
        if team["sources"] and not any(
            source.get("native_rss") or source.get("sports_rss")
            for source in team["sources"]
        ):
            issues.append("no_verified_publisher_native_rss")
        if any(source.get("paywall") is None for source in team["sources"]):
            issues.append("paywall_status_unverified")
        if any(source.get("endpoint_verification", {}).get("errors")
               for source in team["sources"]):
            issues.append("publisher_endpoint_errors")
        if team.get("research_errors"):
            issues.append("research_errors")
        if issues:
            problems.append({
                "team": team["team"], "conference": team.get("conference"),
                "source_count": len(team["sources"]),
                "issues": issues,
                "errors": team.get("research_errors", []),
            })
        for source in team["sources"]:
            endpoints.append({
                "team": team["team"], "publisher": source["name"],
                "domain": source["domain"], "endpoint_type": "GOOGLE_NEWS_RSS",
                "url": source["google_news_rss"], "verified": True,
                "verified_at": source["verification"]["checked_at"],
            })
            for endpoint_type, field in (("NATIVE_RSS", "native_rss"),
                                         ("SPORTS_RSS", "sports_rss"),
                                         ("NEWS_SITEMAP", "news_sitemap")):
                if source.get(field):
                    endpoints.append({
                        "team": team["team"], "publisher": source["name"],
                        "domain": source["domain"], "endpoint_type": endpoint_type,
                        "url": source[field], "verified": True,
                        "verified_at": registry["metadata"].get(
                            "publisher_endpoint_checked_at"),
                    })

    paths = {
        "registry": output / "cfb_local_source_registry.json",
        "publishers": output / "cfb_local_publishers.json",
        "endpoints": output / "cfb_local_machine_endpoints.json",
        "coverage": output / "cfb_local_coverage_report.json",
        "problems": output / "cfb_local_problem_cases.json",
        "summary": output / "cfb_local_source_summary.csv",
    }
    payloads = {
        "registry": registry, "publishers": {"publishers": publishers},
        "endpoints": {"endpoints": endpoints}, "coverage": report,
        "problems": {"problem_cases": problems},
    }
    for key, payload in payloads.items():
        paths[key].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    with paths["summary"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "Team", "Conference", "Primary Source", "Secondary Source",
            "Native RSS Available?", "Fallback Available?", "Confidence",
        ))
        writer.writeheader()
        for team in registry["teams"].values():
            sources = team["sources"]
            writer.writerow({
                "Team": team["team"], "Conference": team.get("conference") or "",
                "Primary Source": sources[0]["name"] if sources else "",
                "Secondary Source": sources[1]["name"] if len(sources) > 1 else "",
                "Native RSS Available?": "yes" if any(s["native_rss"] for s in sources) else "no",
                "Fallback Available?": "yes" if sources else "no",
                "Confidence": sources[0]["confidence"] if sources else "low",
            })
    return paths


def import_source_graph(registry: dict[str, Any], database_path: str | Path) -> dict[str, int]:
    """Project verified publishers, team beats, and endpoints into the source graph."""
    from sports_aggregator.social.models import SourceEndpointProfile, SourceEntityProfile
    from sports_aggregator.social.unified import UnifiedSourceRegistry

    grouped: dict[str, dict[str, Any]] = {}
    for team in registry["teams"].values():
        for source in team["sources"]:
            publisher = grouped.setdefault(source["domain"], {
                "name": source["name"], "domain": source["domain"],
                "source_type": source["source_type"], "teams": set(),
                "sources": [], "high_confidence": False,
            })
            publisher["teams"].add(team["team"])
            publisher["sources"].append((team, source))
            publisher["high_confidence"] |= source["confidence"] == "high"

    graph = UnifiedSourceRegistry(database_path)
    entities = endpoints = 0
    for domain, publisher in sorted(grouped.items()):
        publisher_id = _publisher_id(domain)
        entity_id = graph.upsert_entity(SourceEntityProfile(
            name=publisher["name"], organization=publisher["name"],
            entity_type="ORGANIZATION", entity_key=f"local-publisher:{publisher_id}",
            source_classes=("LOCAL_OUTLET", "PUBLICATION"),
            specialties=("local_reporting", "team_reporting", "injuries", "practice",
                         "depth_chart", "transfers", "recruiting", "coaching"),
            teams=tuple(sorted(publisher["teams"])),
            reliability_score=4 if publisher["high_confidence"] else 3,
            reporting_score=4, team_access_score=5, national_score=1,
            breaking_score=4, priority=4,
            trust_status="VERIFIED_LOCAL_COVERAGE",
        ))
        entities += 1
        seen_urls: set[tuple[str, str]] = set()
        for team, source in publisher["sources"]:
            google_url = source["google_news_rss"]
            endpoint = SourceEndpointProfile(
                platform="rss", endpoint_type="GOOGLE_NEWS_RSS",
                platform_id=google_url, url=google_url,
                endpoint_key=f"rss:google-news:{publisher_id}:{team['team_id']}",
                verification_status="verified",
            )
            graph.upsert_endpoint(entity_id, endpoint); endpoints += 1
            for endpoint_type, field, platform in (
                ("WEBSITE_RSS", "native_rss", "rss"),
                ("SPORTS_RSS", "sports_rss", "rss"),
                ("NEWS_SITEMAP", "news_sitemap", "sitemap"),
            ):
                url = source.get(field)
                if not url or (platform, url) in seen_urls:
                    continue
                seen_urls.add((platform, url))
                graph.upsert_endpoint(entity_id, SourceEndpointProfile(
                    platform=platform, endpoint_type=endpoint_type,
                    platform_id=url, url=url, endpoint_key=f"{platform}:{url}",
                    verification_status="verified",
                ))
                endpoints += 1
    return {"entities": entities, "endpoints": endpoints}
