"""Outbound RSS and sitemap generation.

The aggregation pipeline ingests other publishers' feeds; this module is the
other direction, and the distinction matters for terms of use. Every item
syndicated here links to the **original publisher's URL** and names them in the
``source`` and ``author`` fields. Nothing is rewritten, and no summary is
invented — the feed carries the stored text as ingested. A reader who follows
one of these items lands on the publisher's page, not on a copy.

The sitemap advertises only canonical, stable, publicly meaningful pages. Admin
views, API endpoints, and query-parameter variants are deliberately excluded:
they either duplicate a canonical page or are not meant to be crawled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from xml.sax.saxutils import escape, quoteattr


RSS_ITEM_LIMIT = 50


def _text(value: Any) -> str:
    return escape(" ".join(str(value or "").split()))


def _rfc822(value: Any) -> str:
    """RSS requires RFC-822 dates; stored timestamps are ISO-8601 UTC."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _w3c_date(value: Any) -> str:
    """Sitemaps use W3C datetime; a date-only value is valid and sufficient."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d")


def story_items(stories: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize clustered stories into feed items, dropping unlinkable ones.

    A story with no resolvable original URL cannot be syndicated responsibly —
    there would be nowhere to send the reader and no one to credit — so it is
    omitted rather than pointed at an internal page.
    """
    items = []
    for story in stories:
        url = story.get("url")
        if not url:
            continue
        items.append({
            "title": story.get("title") or story.get("headline_canonical"),
            "link": url,
            "guid": url,
            "published_at": story.get("published_at") or story.get("last_updated_at"),
            "source": story.get("source_name") or story.get("source_display_name"),
            "author": (story.get("sources") or [{}])[0].get("author_name"),
            "description": _story_description(story),
        })
    return items[:RSS_ITEM_LIMIT]


def _story_description(story: dict[str, Any]) -> str:
    """Attribution plus corroboration count — facts the aggregator itself owns.

    The publisher's body text is not reproduced. What this adds is the thing the
    pipeline computed: who reported it and how many independent sources carried
    it.
    """
    source = " ".join(str(story.get("source_name") or "").split())
    others = max(len(story.get("sources") or []) - 1, 0)
    parts = []
    if source:
        parts.append(f"Reported by {source}.")
    if others:
        parts.append(f"{others} corroborating source{'s' if others != 1 else ''}.")
    return " ".join(parts)


def rss_feed(*, title: str, description: str, link: str, self_url: str,
             items: Iterable[dict[str, Any]],
             built_at: datetime | None = None) -> str:
    """A conforming RSS 2.0 document with an atom:link self reference."""
    built = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">',
        "  <channel>",
        f"    <title>{_text(title)}</title>",
        f"    <link>{_text(link)}</link>",
        f"    <description>{_text(description)}</description>",
        "    <language>en-us</language>",
        f"    <lastBuildDate>{built.strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>",
        f"    <atom:link href={quoteattr(self_url)} rel=\"self\""
        ' type="application/rss+xml"/>',
    ]
    for item in items:
        lines.append("    <item>")
        lines.append(f"      <title>{_text(item.get('title'))}</title>")
        lines.append(f"      <link>{_text(item.get('link'))}</link>")
        guid = item.get("guid") or item.get("link")
        # These identifiers are URLs, but they are the publisher's URLs; the
        # feed does not claim them as its own permalinks.
        lines.append(f'      <guid isPermaLink="false">{_text(guid)}</guid>')
        published = _rfc822(item.get("published_at"))
        if published:
            lines.append(f"      <pubDate>{published}</pubDate>")
        if item.get("source"):
            lines.append(f"      <dc:publisher>{_text(item['source'])}</dc:publisher>")
        if item.get("author"):
            lines.append(f"      <dc:creator>{_text(item['author'])}</dc:creator>")
        if item.get("description"):
            lines.append(f"      <description>{_text(item['description'])}</description>")
        lines.append("    </item>")
    lines.extend(["  </channel>", "</rss>", ""])
    return "\n".join(lines)


def sitemap(entries: Iterable[dict[str, Any]]) -> str:
    """A urlset of canonical pages. `entries` carry `loc` and optional `lastmod`."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for entry in entries:
        loc = entry.get("loc")
        if not loc:
            continue
        lines.append("  <url>")
        lines.append(f"    <loc>{_text(loc)}</loc>")
        lastmod = _w3c_date(entry.get("lastmod"))
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        if entry.get("changefreq"):
            lines.append(f"    <changefreq>{_text(entry['changefreq'])}</changefreq>")
        if entry.get("priority") is not None:
            lines.append(f"    <priority>{entry['priority']}</priority>")
        lines.append("  </url>")
    lines.extend(["</urlset>", ""])
    return "\n".join(lines)


def robots(sitemap_url: str) -> str:
    """Permit crawling of pages, keep crawlers out of admin and API surfaces."""
    return "\n".join([
        "User-agent: *",
        "Disallow: /college-football/admin/",
        "Disallow: /api/",
        "Allow: /",
        "",
        f"Sitemap: {sitemap_url}",
        "",
    ])
