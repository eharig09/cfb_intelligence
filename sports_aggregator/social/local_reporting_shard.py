"""Ingest one bounded shard of team-scoped local reporting.

The full registry is hundreds of Google News RSS searches. Running all of them
inside a normal refresh makes throttling turn into a long-lived process and
competes with the web service for memory. This command deliberately does only
one persisted slice, commits what it gets, advances the cursor, and exits.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from dotenv import load_dotenv

from sports_aggregator.models import FeedConfig
from sports_aggregator.providers.rss import RSSNewsProvider
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.local_sources import (
    article_matches_team,
    _publisher_id,
    google_news_url,
)


def _state_path(repository: ContentRepository) -> Path:
    configured = (os.getenv("CFB_LOCAL_NEWS_CURSOR_PATH") or "").strip()
    return Path(configured) if configured else repository.path.parent / "local_news_cursor.json"


def _read_cursor(path: Path) -> int:
    try:
        return max(0, int(json.loads(path.read_text(encoding="utf-8")).get("next", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _write_cursor(path: Path, next_index: int, total: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "next": next_index,
        "total": total,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _manual_tasks(database_path: Path, limit: int) -> list[tuple[dict, dict, FeedConfig]]:
    """Turn Source Admin website/RSS seeds into bounded local-news tasks."""
    if not database_path.exists():
        return []
    tasks: list[tuple[dict, dict, FeedConfig]] = []
    try:
        with closing(sqlite3.connect(database_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required = {"source_entities", "source_endpoints", "source_entity_teams", "teams"}
            if not required.issubset(tables):
                return []
            rows = connection.execute(
                """SELECT e.source_entity_id,e.entity_key,e.name,e.reliability_score,
                          ep.endpoint_key,ep.platform,ep.url,st.team,
                          t.team_id,t.school,t.mascot,t.abbreviation,t.conference,t.classification
                   FROM source_endpoints ep
                   JOIN source_entities e USING(source_entity_id)
                   JOIN source_entity_teams st USING(source_entity_id)
                   JOIN teams t ON t.school=st.team
                   WHERE e.active=1 AND ep.active=1
                     AND ep.platform IN ('website','rss')
                     AND ep.url IS NOT NULL
                   ORDER BY e.priority DESC,e.name,t.school"""
            ).fetchall()
            for row in rows:
                item = dict(row)
                parsed = urlsplit(item["url"])
                domain = (parsed.hostname or "").casefold().removeprefix("www.")
                if not domain:
                    continue
                team = {
                    "team_id": int(item["team_id"]),
                    "team": item["school"],
                    "school": item["school"],
                    "mascot": item.get("mascot"),
                    "abbreviation": item.get("abbreviation"),
                    "conference": item.get("conference"),
                    "division": item.get("classification"),
                }
                source = {
                    "name": item["name"],
                    "domain": domain,
                    "confidence": "high" if int(item.get("reliability_score") or 3) >= 4 else "medium",
                }
                if item["platform"] == "rss":
                    feed_url = item["url"]
                    endpoint_key = item["endpoint_key"]
                else:
                    # A manually supplied publisher URL is exact publisher
                    # identity, but not necessarily a feed. Use the same safe
                    # Google News domain fallback as the researched registry.
                    query = f'"{item["school"]} football" site:{domain}'
                    feed_url = google_news_url(query)
                    endpoint_key = f"rss:manual-google-news:{item['source_entity_id']}:{item['team_id']}"
                tasks.append((
                    team,
                    source,
                    FeedConfig(
                        name=item["name"],
                        url=feed_url,
                        max_articles=limit,
                        source_type="local_reporting",
                        reliability=max(1, min(5, int(item.get("reliability_score") or 3))),
                        source_entity_key=item["entity_key"],
                        source_endpoint_key=endpoint_key,
                    ),
                ))
    except sqlite3.Error:
        return []
    return tasks


def _tasks(limit: int, database_path: Path) -> list[tuple[dict, dict, FeedConfig]]:
    registry_path = Path("data/local_sources/cfb_local_source_registry.json")
    tasks: list[tuple[dict, dict, FeedConfig]] = []
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        for team in registry["teams"].values():
            for source in team["sources"]:
                publisher_id = _publisher_id(source["domain"])
                tasks.append((
                    team,
                    source,
                    FeedConfig(
                        name=source["name"],
                        url=source["google_news_rss"],
                        max_articles=limit,
                        source_type="local_reporting",
                        reliability=4 if source["confidence"] == "high" else 3,
                        source_entity_key=f"local-publisher:{publisher_id}",
                        source_endpoint_key=f"rss:google-news:{publisher_id}:{team['team_id']}",
                    ),
                ))

    # Manual entries are additive. De-duplicate identical team/feed pairs so a
    # source already present in the researched registry is not polled twice.
    seen = {(int(team["team_id"]), config.url) for team, _, config in tasks}
    for task in _manual_tasks(database_path, limit):
        team, _, config = task
        key = (int(team["team_id"]), config.url)
        if key in seen:
            continue
        seen.add(key)
        tasks.append(task)
    return tasks


def run_shard(season: int, *, shard_size: int, workers: int, limit: int) -> dict:
    repository = ContentRepository(os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    tasks = _tasks(limit, repository.path)
    if not tasks:
        return {"status": "empty", "total": 0, "stored": 0}

    state = _state_path(repository)
    start = _read_cursor(state)
    if start >= len(tasks):
        start = 0
    end = min(start + max(1, shard_size), len(tasks))
    selected = tasks[start:end]

    started = datetime.now(timezone.utc).isoformat()
    errors: list[dict] = []
    succeeded = seen = stored = 0

    def fetch(task):
        team, source, config = task
        return team, source, RSSNewsProvider(config).fetch()

    # A small pool is intentional. Google News throttles datacenter bursts and
    # this job values bounded resource use over wall-clock speed.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch, task): task for task in selected}
        for future in as_completed(futures):
            team, source, _ = futures[future]
            try:
                _, _, articles = future.result()
                succeeded += 1
                seen += len(articles)
                for article in articles:
                    if not article_matches_team(
                        f"{article.title} {article.summary}",
                        team,
                        publisher=article.publisher or source["name"],
                    ):
                        continue
                    enriched = replace(article, team_ids=(int(team["team_id"]),))
                    if repository.store_article(enriched, season) is not None:
                        stored += 1
            except Exception as exc:
                errors.append({
                    "team": team["team"],
                    "domain": source["domain"],
                    "error": str(exc)[:240],
                })

    # Move forward even when a publisher is temporarily unavailable. It gets
    # another chance on the next full cycle instead of blocking every later
    # source behind it.
    next_index = 0 if end >= len(tasks) else end
    _write_cursor(state, next_index, len(tasks))
    repository.record_run(
        started,
        datetime.now(timezone.utc).isoformat(),
        len(selected),
        succeeded,
        seen,
        stored,
        errors,
        platform="rss-local-shard",
    )
    return {
        "status": "success" if succeeded else "failed",
        "start": start,
        "end": end,
        "next": next_index,
        "total": len(tasks),
        "attempted": len(selected),
        "succeeded": succeeded,
        "seen": seen,
        "stored": stored,
        "errors": len(errors),
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ingest one local-reporting shard")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--shard-size", type=int,
                        default=int(os.getenv("CFB_LOCAL_NEWS_SHARD_SIZE", "50")))
    parser.add_argument("--workers", type=int,
                        default=int(os.getenv("CFB_LOCAL_NEWS_WORKERS", "2")))
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)
    report = run_shard(args.season, shard_size=args.shard_size,
                       workers=args.workers, limit=args.limit)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"success", "empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
