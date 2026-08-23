from flask import Blueprint, render_template
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import os
from dotenv import load_dotenv
import logging
import time
import random


load_dotenv()

bengals = Blueprint('bengals', __name__, template_folder='../templates')

logging.basicConfig(level=logging.INFO)

cached_token = None

def bluesky_login():
    global cached_token
    if cached_token:
        logging.info("Using cached Bluesky token.")
        return cached_token

    url = "https://bsky.social/xrpc/com.atproto.server.createSession"
    data = {
        "identifier": os.getenv("BLUESKY_USERNAME"),
        "password": os.getenv("BLUESKY_PASSWORD")
    }

    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        cached_token = response.json().get("accessJwt")
        logging.info("Successfully authenticated with Bluesky.")
        return cached_token
    except requests.RequestException as e:
        logging.error(f"Failed to authenticate with Bluesky: {e}")
        return None


def aggregate_news():
    news_sources = [scrape_espn, scrape_fox, scrape_cbs, scrape_sbnation, scrape_athletic]
    news = []
    for source in news_sources:
        try:
            source_news = source()
            news.extend(source_news)
            logging.info(f"Fetched {len(source_news)} articles from {source.__name__}.")
        except Exception as e:
            logging.error(f"Error while scraping with {source.__name__}: {e}")
    sorted_news = sort_articles(news)
    logging.info(f"Total aggregated articles: {len(sorted_news)}")

    token = bluesky_login()
    skeets = fetch_bengals_skeets(token) if token else []

    return sorted_news[:25], skeets[:25]


@bengals.route('/')
def home():
    news, skeets = aggregate_news()
    return render_template(
        'bengals.html',
        news=news,
        skeets=skeets
    )





DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

def get_soup(url, headers=None, timeout=15, retries=3, backoff=1.5):
    headers = headers or DEFAULT_HEADERS

    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            # Helpful debug
            if resp.status_code in (403, 429):
                print(f"[get_soup] {resp.status_code} for {url}")
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            last_err = e
            sleep_s = (backoff ** attempt) + random.random() * 0.25
            time.sleep(sleep_s)

    print(f"[get_soup] FAILED {url} after {retries} tries: {last_err}")
    return None



def sort_articles(articles):
    def parse_date(date_str):
        try:
            # Try parsing the standard format first (YYYY-MM-DD HH:MM:SS)
            date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            return date
        except ValueError:
            try:
                # Try parsing the SB Nation format (ISO 8601)
                date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
                return date
            except ValueError:
                try:
                    # Attempt to parse CBS format if not in standard format
                    date = datetime.strptime(date_str.strip(), "%b %d, %Y")
                    return date
                except ValueError:
                    try:
                        # Attempt to parse FanGraphs format (e.g., 'March 23, 2025')
                        date = datetime.strptime(date_str.strip(), "%B %d, %Y")
                        return date
                    except ValueError:
                        # Handle relative dates like "2 days ago" or "16h"
                        now = datetime.now()

                        match = re.search(r"(\d+) day", date_str)
                        if match:
                            days = int(match.group(1))
                            return now - timedelta(days=days)

                        match = re.search(r"(\d+) hour", date_str)
                        if match:
                            hours = int(match.group(1))
                            return now - timedelta(hours=hours)

                        match = re.search(r"(\d+) minute", date_str)
                        if match:
                            minutes = int(match.group(1))
                            return now - timedelta(minutes=minutes)

                        match = re.search(r"(\d+) second", date_str)
                        if match:
                            seconds = int(match.group(1))
                            return now - timedelta(seconds=seconds)

                        match = re.search(r"(\d+)h", date_str)
                        if match:
                            hours = int(match.group(1))
                            return now - timedelta(hours=hours)

                        match = re.search(r"(\d+)d", date_str)
                        if match:
                            days = int(match.group(1))
                            return now - timedelta(days=days)

                        match = re.search(r"(\d+)m", date_str)
                        if match:
                            minutes = int(match.group(1))
                            return now - timedelta(minutes=minutes)

                        match = re.search(r"(\d+)s", date_str)
                        if match:
                            seconds = int(match.group(1))
                            return now - timedelta(seconds=seconds)

                        # Fallback to a very old date for unrecognized formats
                        print(f"Fallback date (unknown format): {date_str} -> {datetime.min}")
                        return datetime.min


    # Sort articles by parsed date, with most recent first
    sorted_articles = sorted(articles, key=lambda x: parse_date(x['date']), reverse=True)


    return sorted_articles



def scrape_espn():
    url = "https://www.espn.com/nfl/team/_/name/cin/cincinnati-bengals"
    soup = get_soup(url)
    news = []

    if soup:
        articles = soup.find_all('a', class_='contentItem__content')
        for link in articles:
            title = link.get_text().strip()
            url = link['href']

            # Extract author and date from sibling elements or parent
            author = "Unknown Author"
            date = "Unknown Date"
            meta = link.find('ul', class_='contentItem__publicationMeta')
            if meta:
                date_tag = meta.find('li', class_='time-elapsed')
                author_tag = meta.find('li', class_='author')
                if date_tag:
                    date = date_tag.get_text()
                if author_tag:
                    author = author_tag.get_text()

            news.append({
                "title": title,
                "url": url if url.startswith("https") else f"https://www.espn.com{url}",
                "author": author,
                "source": "ESPN",
                "date": date
            })

    print(f"Found {len(news)} ESPN articles.")
    return news





FOX_MLB_RSS = "https://api.foxsports.com/v2/content/optimized-rss?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=50&tags=fs%2Fmlb"

def scrape_fox():
    news = []
    feed = feedparser.parse(FOX_MLB_RSS)
    entries = getattr(feed, "entries", []) or []

    for e in entries:
        title = getattr(e, "title", "")
        link = getattr(e, "link", "")
        if "reds" not in title.lower() and "cincinnati" not in title.lower():
            continue

        date = getattr(e, "published", getattr(e, "updated", ""))
        news.append({
            "title": title or "No Title",
            "url": link,
            "author": "Fox Sports",
            "source": "Fox Sports",
            "date": date or "Unknown Date"
        })

    print(f"Found {len(news)} Fox Sports RSS articles (filtered for Reds).")
    return news




def scrape_cbs():
    url = "https://www.cbssports.com/nfl/teams/CIN/cincinnati-bengals/"
    soup = get_soup(url)
    news = []

    if soup:
        articles = soup.find_all('article', class_='NewsFeed-container')
        for article in articles:
            link = article.find('a', href=True)
            if link:
                title = article.find_next('h3').get_text().strip()
                url = link['href']

                # Ensure the URL is complete
                if not url.startswith("https"):
                    url = f"https://www.cbssports.com{url}"

                # Extracting author and date (not in the same tag as before)
                author = "Unknown Author"
                date = "Unknown Date"

                # Find parent container or sibling for author and date
                parent = article.find_next('div', class_='NewsFeed-byline')
                if parent:
                    author_tag = parent.find('span', class_='NewsFeed-author')
                    date_tag = parent.find('time')
                    if author_tag:
                        author = author_tag.get_text()
                    if date_tag:
                        date = date_tag.get_text()

                news.append({
                    "title": title,
                    "url": url,
                    "author": author,
                    "source": "CBS Sports",
                    "date": date
                })

    print(f"Found {len(news)} CBS articles.")
    return news



import feedparser
from datetime import datetime

def scrape_sbnation():
    feed_url = "https://www.redreporter.com/rss/current.xml"
    news = []

    feed = feedparser.parse(feed_url)
    if getattr(feed, "bozo", 0):
        print(f"Red Reporter RSS parse issue: {getattr(feed, 'bozo_exception', '')}")

    entries = getattr(feed, "entries", []) or []
    for e in entries[:20]:
        title = getattr(e, "title", "No Title")
        url = getattr(e, "link", "")
        author = getattr(e, "author", "Red Reporter")
        # dates vary by feed; try a few common fields
        date = ""
        if hasattr(e, "published"):
            date = e.published
        elif hasattr(e, "updated"):
            date = e.updated
        else:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        news.append({
            "title": title,
            "url": url,
            "author": author,
            "source": "Red Reporter",
            "date": date
        })

    print(f"Found {len(news)} Red Reporter RSS entries.")
    return news



def extract_date_from_url(url):
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
    if match:
        year, month, day = match.groups()
        date = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
        return date.strftime("%Y-%m-%d %H:%M:%S")
    return "Unknown Date"


def scrape_athletic():
    url = "https://www.nytimes.com/athletic/nfl/team/bengals/"
    soup = get_soup(url)
    news = []

    if soup:
        articles = soup.find_all('div', class_='sc-67a2781f-0')
        for article in articles:
            # Extract the link and title
            link_tag = article.find('a', href=True)
            if link_tag:
                url = link_tag['href']
                title_tag = link_tag.find('span')
                title = title_tag.get_text().strip() if title_tag else "No Title"
            else:
                url = "No URL"
                title = "No Title"
            
            # Extract the author(s)
            author_tag = article.find('p', class_='sc-4ec04b8c-0 hDGiPh')
            author = author_tag.get_text().strip() if author_tag else "Unknown Author"
            
            # Extract the date (if available)
            date = extract_date_from_url(url)
            
            news.append({
                "title": title,
                "url": url,
                "author": author,
                "source": "The Athletic",
                "date": date
            })

    print(f"Found {len(news)} articles from The Athletic.")
    return news




USERNAME = os.getenv("BLUESKY_USERNAME")
PASSWORD = os.getenv("BLUESKY_PASSWORD")

def bluesky_login():
    """Authenticate and return the JWT token."""
    url = "https://bsky.social/xrpc/com.atproto.server.createSession"
    data = {
        "identifier": USERNAME,
        "password": PASSWORD
    }

    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        token = response.json().get("accessJwt")
        print("Successfully authenticated with Bluesky.")
        return token
    except requests.RequestException as e:
        print(f"Failed to authenticate with Bluesky: {e}")
        return None




def sanitize_skeet(skeet):
    for key in ['author', 'text', 'timestamp']:
        if key not in skeet or skeet[key] is None:
            skeet[key] = "Unknown"
    return skeet

import re

def extract_links(text):
    """
    Extracts URLs from the given text.
    """
    url_regex = r'(https?://\S+)'
    return re.findall(url_regex, text)

def parse_timestamp(timestamp):
    """Try to parse the timestamp with different formats."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(timestamp, fmt)
        except ValueError:
            continue
    print(f"Failed to parse timestamp: {timestamp}")
    return datetime.min  # Return the earliest possible date if parsing fails

token = None
logging.basicConfig(level=logging.INFO)

def fetch_bengals_skeets(token):
    skeets = []
    try:
        usernames = [
            "pauldehnerjr.bsky.social",
            "jaymorrison.bsky.social",
            "jakeliscow.bsky.social",
            "bengais.bsky.social",
            "peterking1.bsky.social",
            "adamschefter.bsky.social",
            "rapsheet1.bsky.social",
            "tompelissero.bsky.social",
            "diannarussini.bsky.social",
            "fieldyates.bsky.social",
            "minakimes.bsky.social",
            "rbsdm.com"
        ]

        for username in usernames:
            response = requests.get(
                f"https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed?actor={username}",
                headers={"Authorization": f"Bearer {token}"}
            )

            if response.status_code == 200:
                data = response.json()
                for skeet in data.get("feed", []):
                    author = skeet.get("post", {}).get("author", {}).get("handle", "Unknown")
                    display_name = skeet.get("post", {}).get("author", {}).get("displayName", "Unknown")
                    avatar_url = skeet.get("post", {}).get("author", {}).get("avatar", "")
                    text = skeet.get("post", {}).get("record", {}).get("text", "No Text")
                    timestamp = skeet.get("post", {}).get("record", {}).get("createdAt", "Unknown Timestamp")

                    # Handle quote skeet (context skeet)
                    quote_skeet = None
                    embed = skeet.get("post", {}).get("record", {}).get("embed", {})
                    if embed.get("$type") == "app.bsky.embed.recordWithMedia":
                        embedded_record = embed.get("record", {}).get("record", {})
                        quoted_author = embedded_record.get("author", {}).get("handle", "Unknown")
                        quoted_display_name = embedded_record.get("author", {}).get("displayName", "Unknown")
                        quoted_text = embedded_record.get("text", "No Quoted Text")
                        quote_skeet = {
                            "author": quoted_author,
                            "display_name": quoted_display_name,
                            "text": quoted_text
                        }

                    # Handle media (images)
                    media = []
                    if embed.get("$type") == "app.bsky.embed.images":
                        images = embed.get("images", [])
                        for image in images:
                            image_link = image.get("image", {}).get("ref", {}).get("$link", "")
                            if image_link:
                                full_image_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:b2kutgxqlltwc6lhs724cfwr/{image_link}"
                                media.append(full_image_url)

                    # Convert URLs in text to clickable links
                    text = re.sub(r"(https?://\S+)", r'<a href="\1" target="_blank">\1</a>', text)

                    skeets.append({
                        "author": author,
                        "display_name": display_name,
                        "avatar_url": avatar_url,
                        "text": text,
                        "timestamp": timestamp,
                        "media": media,
                        "quote_skeet": quote_skeet  # Include the quote skeet if present
                    })
            else:
                print(f"Failed to fetch skeets from {username}: {response.status_code}")

        # Sort skeets by most recent timestamp using the flexible parser
        skeets.sort(key=lambda x: parse_timestamp(x['timestamp']), reverse=True)

        print(f"Total Bengals-related skeets found: {len(skeets)}")
    except Exception as e:
        print(f"Failed to fetch Bengals skeets: {e}")

    return skeets



def sanitize_article(article):
    for key in ['title', 'url', 'author', 'source', 'date']:
        if key not in article or article[key] is None:
            article[key] = "Unknown"
    return article

def aggregate_news():
    news_sources = [
        scrape_espn,
        scrape_fox,
        scrape_cbs,
        scrape_sbnation,
        scrape_athletic,
    ]
    news = []
    for source in news_sources:
        source_news = source()
        for article in source_news:
            news.append(sanitize_article(article))
    sorted_news = sort_articles(news)
    print(f"Total aggregated articles: {len(sorted_news)}")
    
    # Get the token
    token = bluesky_login()
    skeets = fetch_bengals_skeets(token)

    
    return sorted_news[:25], skeets[:25]



