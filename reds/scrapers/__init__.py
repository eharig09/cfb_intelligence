"""
scrapers/__init__.py — news scraping from ESPN, Fox, CBS, Red Reporter,
FanGraphs, and The Athletic. Each scraper returns a list of article dicts:
    {"title", "url", "author", "source", "date"}
"""

import os
import re
import json
import html
from datetime import datetime
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from reds.utils import get_soup, DEFAULT_HEADERS, news_cache

ATHLETIC_COOKIE_HEADER = os.getenv("ATHLETIC_COOKIES", "")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_cookie_header(cookie_header: str) -> dict:
    cookies = {}
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def _find_next_data_json(html_text: str) -> Optional[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None


def _walk(obj):
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _normalize_athletic_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://theathletic.com" + u
    return u


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

def scrape_espn() -> list:
    cached = news_cache.get("espn")
    if cached is not None:
        return cached

    url = "https://www.espn.com/mlb/team/_/name/cin/cincinnati-reds"
    soup = get_soup(url)
    news = []

    if soup:
        for link in soup.find_all("a", class_="contentItem__content"):
            title = link.get_text().strip()
            href = link.get("href", "")
            author, date = "Unknown Author", "Unknown Date"

            meta = link.find("ul", class_="contentItem__publicationMeta")
            if meta:
                date_tag = meta.find("li", class_="time-elapsed")
                author_tag = meta.find("li", class_="author")
                if date_tag:
                    date = date_tag.get_text()
                if author_tag:
                    author = author_tag.get_text()

            full_url = href if href.startswith("https") else f"https://www.espn.com{href}"
            news.append({"title": title, "url": full_url, "author": author, "source": "ESPN", "date": date})

    print(f"[ESPN] {len(news)} articles")
    news_cache.set("espn", news)
    return news


# ---------------------------------------------------------------------------
# Fox Sports (RSS)
# ---------------------------------------------------------------------------

_FOX_RSS = (
    "https://api.foxsports.com/v2/content/optimized-rss"
    "?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=80"
    "&tags=fs%2Fmlb"
)


def scrape_fox() -> list:
    cached = news_cache.get("fox")
    if cached is not None:
        return cached

    feed = feedparser.parse(_FOX_RSS)
    news = []

    for e in getattr(feed, "entries", []) or []:
        title = getattr(e, "title", "") or ""
        link = getattr(e, "link", "") or ""
        summary = getattr(e, "summary", "") or ""
        blob = f"{title} {summary}".lower()
        if "reds" not in blob and "cincinnati" not in blob:
            continue

        date = getattr(e, "published", getattr(e, "updated", ""))
        news.append({
            "title": title or "No Title",
            "url": link,
            "author": "Fox Sports",
            "source": "Fox Sports",
            "date": date or "Unknown Date",
        })

    print(f"[Fox] {len(news)} articles")
    news_cache.set("fox", news)
    return news


# ---------------------------------------------------------------------------
# CBS Sports
# ---------------------------------------------------------------------------

def scrape_cbs() -> list:
    cached = news_cache.get("cbs")
    if cached is not None:
        return cached

    url = "https://www.cbssports.com/mlb/teams/CIN/cincinnati-reds/"
    soup = get_soup(url)
    news = []

    if soup:
        for article in soup.find_all("article", class_="NewsFeed-container"):
            link = article.find("a", href=True)
            if not link:
                continue

            title_tag = article.find_next("h3")
            title = title_tag.get_text().strip() if title_tag else "No Title"
            href = link["href"]
            full_url = href if href.startswith("https") else f"https://www.cbssports.com{href}"
            author, date = "Unknown Author", "Unknown Date"

            byline = article.find_next("div", class_="NewsFeed-byline")
            if byline:
                a_tag = byline.find("span", class_="NewsFeed-author")
                d_tag = byline.find("time")
                if a_tag:
                    author = a_tag.get_text()
                if d_tag:
                    date = d_tag.get_text()

            news.append({"title": title, "url": full_url, "author": author, "source": "CBS Sports", "date": date})

    print(f"[CBS] {len(news)} articles")
    news_cache.set("cbs", news)
    return news


# ---------------------------------------------------------------------------
# Red Reporter (SB Nation RSS)
# ---------------------------------------------------------------------------

def scrape_sbnation() -> list:
    cached = news_cache.get("sbnation")
    if cached is not None:
        return cached

    feed = feedparser.parse("https://www.redreporter.com/rss/current.xml")
    if getattr(feed, "bozo", 0):
        print(f"[RedReporter] RSS parse issue: {getattr(feed, 'bozo_exception', '')}")

    news = []
    for e in (getattr(feed, "entries", []) or [])[:20]:
        date = getattr(e, "published", getattr(e, "updated", datetime.utcnow().strftime("%Y-%m-%d")))
        news.append({
            "title": getattr(e, "title", "No Title"),
            "url": getattr(e, "link", ""),
            "author": getattr(e, "author", "Red Reporter"),
            "source": "Red Reporter",
            "date": date,
        })

    print(f"[Red Reporter] {len(news)} articles")
    news_cache.set("sbnation", news)
    return news


# ---------------------------------------------------------------------------
# FanGraphs
# ---------------------------------------------------------------------------

def scrape_fangraphs() -> list:
    cached = news_cache.get("fangraphs")
    if cached is not None:
        return cached

    soup = get_soup("https://blogs.fangraphs.com/")
    news = []

    if soup:
        for post in soup.find_all("div", class_="post"):
            a = post.find_next("a")
            if not a:
                continue
            title = a.get_text().strip()
            href = a.get("href", "")
            author, date = "Unknown Author", "Unknown Date"

            author_tag = post.find("div", class_="postmeta_author")
            if author_tag:
                author = author_tag.get_text(strip=True)
            divs = post.find_all("div")
            if len(divs) >= 3:
                date = divs[2].get_text(strip=True)

            news.append({"title": title, "url": href, "author": author, "source": "FanGraphs", "date": date})

    print(f"[FanGraphs] {len(news)} articles")
    news_cache.set("fangraphs", news)
    return news


# ---------------------------------------------------------------------------
# The Athletic
# ---------------------------------------------------------------------------

def scrape_athletic() -> list:
    cached = news_cache.get("athletic")
    if cached is not None:
        return cached

    cookies = _parse_cookie_header(ATHLETIC_COOKIE_HEADER)
    if not cookies:
        print("[Athletic] Cookies not set; skipping.")
        return []

    url = "https://theathletic.com/mlb/team/reds/"
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, cookies=cookies, timeout=20)
    except Exception as e:
        print(f"[Athletic] Request failed: {e}")
        return []

    print(f"[Athletic] HTTP {resp.status_code}, len={len(resp.text)}")
    if resp.status_code != 200:
        return []

    article_url_re = re.compile(r"theathletic\.com/\d{6,9}/")
    data = _find_next_data_json(resp.text)
    news = []

    title_keys = ("title", "headline", "label", "name")
    url_keys = ("url", "href", "permalink", "canonicalUrl", "canonical_url", "shareUrl", "webUrl")

    if data is not None:
        for node in _walk(data):
            if not isinstance(node, dict):
                continue

            title = None
            for tk in title_keys:
                v = node.get(tk)
                if isinstance(v, str) and len(v.strip()) >= 12:
                    title = html.unescape(v.strip())
                    break
            if not title:
                continue

            found_url = None
            for uk in url_keys:
                v = node.get(uk)
                if isinstance(v, str) and v:
                    candidate = _normalize_athletic_url(v)
                    if article_url_re.search(candidate):
                        found_url = candidate
                        break

            if not found_url:
                link = node.get("link") or node.get("links")
                if isinstance(link, dict):
                    for uk in url_keys:
                        v = link.get(uk)
                        if isinstance(v, str):
                            candidate = _normalize_athletic_url(v)
                            if article_url_re.search(candidate):
                                found_url = candidate
                                break

            if found_url:
                news.append({
                    "title": title,
                    "url": found_url,
                    "author": "The Athletic",
                    "source": "The Athletic",
                    "date": "Unknown Date",
                })

        news = list({item["url"]: item for item in news}.values())

    if not news:
        urls = set(re.findall(r"https://theathletic\.com/\d{6,9}/[^\"\s<>]+", resp.text))
        rels = {
            "https://theathletic.com" + r.strip().strip('"')
            for r in re.findall(r'"/\d{6,9}/[^\"\s<>]+"\s', resp.text)
        }
        all_urls = sorted(u for u in (urls | rels) if article_url_re.search(u))[:25]
        news = [
            {"title": "(Athletic article)", "url": u, "author": "The Athletic",
             "source": "The Athletic", "date": "Unknown Date"}
            for u in all_urls
        ]

    news = [n for n in news if article_url_re.search(n["url"])][:20]
    print(f"[Athletic] {len(news)} articles")
    news_cache.set("athletic", news)
    return news


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

ALL_SCRAPERS = [
    scrape_espn,
    scrape_fox,
    scrape_cbs,
    scrape_sbnation,
    scrape_fangraphs,
    scrape_athletic,
]
