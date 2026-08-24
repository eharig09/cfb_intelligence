"""CLI for validating and promoting curated YouTube and podcast candidates."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from urllib.parse import urlparse

from dotenv import load_dotenv

from sports_aggregator.providers.podcast import PodcastRSSClient
from sports_aggregator.providers.youtube import YouTubeDataClient
from sports_aggregator.social.media import (
    MediaRegistry,
    PodcastDirectoryClient,
    name_agreement,
    score_channel,
    score_podcast,
)
from sports_aggregator.social.unified import UnifiedSourceRegistry


def _classes(candidate: dict) -> set[str]:
    return {
        item.strip()
        for item in (candidate.get("proposed_classes") or "").split(",")
        if item.strip()
    }


def _youtube_identity(url: str | None) -> tuple[str | None, str | None]:
    """Return a stable channel ID or handle; legacy /c URLs remain search-only."""
    if not url:
        return None, None
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel" and parts[1].startswith("UC"):
        return parts[1], None
    handle = next((part for part in parts if part.startswith("@")), None)
    return None, handle


def _stale_podcast_blocker(candidate: dict, episodes: list) -> str | None:
    dates = [episode.published_at for episode in episodes if episode.published_at]
    if not dates:
        return "feed has no dated episodes"
    age = (datetime.now(timezone.utc) - max(dates)).days
    maximum = 210 if candidate.get("active_status") == "seasonal_review" else 330
    if age > maximum:
        return f"latest feed episode is {age} days old"
    return None


def validate_youtube(registry: MediaRegistry, limit: int, *, force: bool = False,
                     exact_only: bool = False) -> int:
    client = YouTubeDataClient()
    if not client.api_key:
        print("youtube skipped: set YOUTUBE_API_KEY (or YOUTUBE_API) to validate channels")
        return 0
    promoted = reviewed = errors = 0
    for candidate in registry.pending_candidates("youtube", force=force):
        if "YOUTUBE_SHOW" not in _classes(candidate):
            continue
        if exact_only and not candidate.get("youtube_url"):
            continue
        try:
            channel_id, handle = _youtube_identity(candidate.get("youtube_url"))
            curated = bool(channel_id or handle)
            if curated:
                resolution = client.resolve_channel(
                    endpoint_key=f"youtube:candidate:{candidate['candidate_id']}",
                    channel_id=channel_id, handle=handle,
                )
                channels = client.channels([resolution.platform_id]) if (
                    resolution.status == "verified" and resolution.platform_id) else []
            else:
                channels = client.search_channels(candidate["name"], max_results=limit)
            matches = [score_channel(candidate["name"], channel,
                                     curated_identity=curated) for channel in channels]
            registry.record_matches(candidate["candidate_id"], "youtube", matches)
            promotable = sorted(
                (match for match in matches if match["promotable"]),
                key=lambda match: match["score"], reverse=True,
            )
            if promotable:
                best = promotable[0]
                registry.promote_channel(candidate, best)
                registry.record_attempt(candidate["candidate_id"], "youtube", "promoted",
                                        f"channel {best['channel_id']}")
                promoted += 1
                print(
                    f"youtube {candidate['name']}: PROMOTED -> {best['title']} "
                    f"({best['channel_id']}, score={best['score']:.3f})"
                )
            else:
                reviewed += 1
                best = max(matches, key=lambda match: match["score"], default=None)
                note = "no YouTube search results"
                if best:
                    blockers = "; ".join(best["blockers"]) or "below promotion threshold"
                    note = f"best YouTube match {best['title']} score={best['score']:.3f}: {blockers}"
                registry.mark_needs_review(candidate["candidate_id"], note)
                registry.record_attempt(candidate["candidate_id"], "youtube", "review", note)
                print(f"youtube {candidate['name']}: REVIEW ({note})")
        except Exception as exc:
            errors += 1
            registry.mark_needs_review(candidate["candidate_id"], f"YouTube validation error: {exc}")
            registry.record_attempt(candidate["candidate_id"], "youtube", "error", str(exc))
            print(f"youtube {candidate['name']}: ERROR ({exc})")
    print(f"youtube promoted={promoted} review={reviewed} errors={errors}")
    return 0 if errors == 0 else 1


def validate_podcasts(registry: MediaRegistry, limit: int, *, force: bool = False,
                      exact_only: bool = False) -> int:
    directory = PodcastDirectoryClient()
    rss = PodcastRSSClient()
    promoted = reviewed = errors = 0
    for candidate in registry.pending_candidates("podcast", force=force):
        if "PODCAST" not in _classes(candidate):
            continue
        if exact_only and not candidate.get("podcast_url"):
            continue
        try:
            exact_feed = (candidate.get("podcast_url") or "").strip()
            resolution_error = ""
            if exact_feed:
                resolved = rss.resolve(
                    exact_feed, f"podcast:candidate:{candidate['candidate_id']}"
                )
                resolution_error = resolved.description
                episodes = rss.episodes(exact_feed, limit=25) if resolved.status == "verified" else []
                shows = [{
                    "name": resolved.display_name or candidate["name"],
                    "artist": "catalog-verified feed",
                    "feed_url": exact_feed,
                    "genres": ["Sports"],
                    "episode_count": len(episodes),
                }] if resolved.status == "verified" else []
            else:
                episodes = []
                shows = directory.search(candidate["name"], limit=limit)
            matches = [score_podcast(candidate["name"], show) for show in shows]
            if exact_feed and matches:
                blocker = _stale_podcast_blocker(candidate, episodes)
                if blocker:
                    matches[0]["blockers"].append(blocker)
                    matches[0]["promotable"] = False
            registry.record_matches(candidate["candidate_id"], "podcast", matches)
            promotable = sorted(
                (match for match in matches if match["promotable"]),
                key=lambda match: match["score"], reverse=True,
            )
            chosen = None
            feed_title = ""
            for match in promotable:
                result = rss.resolve(
                    match["channel_id"],
                    f"podcast:candidate:{candidate['candidate_id']}",
                )
                if result.status != "verified":
                    continue
                if name_agreement(candidate["name"], result.display_name or "") < 0.85:
                    continue
                chosen = match
                feed_title = result.display_name or match["title"] or candidate["name"]
                break
            if chosen:
                registry.promote_podcast(candidate, chosen, feed_title)
                registry.record_attempt(candidate["candidate_id"], "podcast", "promoted",
                                        f"feed {chosen['channel_id']}")
                promoted += 1
                print(
                    f"podcast {candidate['name']}: PROMOTED -> {feed_title} "
                    f"(score={chosen['score']:.3f})"
                )
            else:
                reviewed += 1
                best = max(matches, key=lambda match: match["score"], default=None)
                note = (f"exact podcast feed failed: {resolution_error}"
                        if exact_feed else
                        "no podcast directory results")
                if best:
                    blockers = "; ".join(best["blockers"]) or "feed identity check failed"
                    note = f"best podcast match {best['title']} score={best['score']:.3f}: {blockers}"
                registry.mark_needs_review(candidate["candidate_id"], note)
                registry.record_attempt(candidate["candidate_id"], "podcast", "review", note)
                print(f"podcast {candidate['name']}: REVIEW ({note})")
        except Exception as exc:
            errors += 1
            registry.mark_needs_review(candidate["candidate_id"], f"Podcast validation error: {exc}")
            registry.record_attempt(candidate["candidate_id"], "podcast", "error", str(exc))
            print(f"podcast {candidate['name']}: ERROR ({exc})")
    print(f"podcast promoted={promoted} review={reviewed} errors={errors}")
    return 0 if errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Validate curated CFB media candidates")
    parser.add_argument(
        "command",
        choices=("seed", "validate-youtube", "validate-podcasts", "validate-all", "status"),
    )
    parser.add_argument("--limit", type=int, default=5, help="Directory/search results per candidate")
    parser.add_argument("--force", action="store_true",
                        help="Retry candidates checked within the last seven days")
    parser.add_argument("--exact-only", action="store_true",
                        help="Validate only catalog records with a researched stable URL")
    parser.add_argument("--details", action="store_true",
                        help="Include candidates, attempts and endpoint details in status JSON")
    args = parser.parse_args(argv)

    database = os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    registry = MediaRegistry(database)

    if args.command == "seed":
        print(f"media_candidates={UnifiedSourceRegistry(database).seed_media_candidates()}")
        return 0

    if args.command == "status":
        status = registry.status()
        if args.details:
            print(json.dumps(status, indent=2, default=str))
            return 0
        summary = {
            "candidates": len(status["candidates"]),
            "youtube_endpoints": len(status["youtube_endpoints"]),
            "podcast_endpoints": len(status["podcast_endpoints"]),
            "matches": len(status["matches"]),
            "candidate_statuses": {},
        }
        for candidate in status["candidates"]:
            key = candidate["validation_status"]
            summary["candidate_statuses"][key] = summary["candidate_statuses"].get(key, 0) + 1
        print(json.dumps(summary, indent=2))
        return 0

    youtube_result = podcast_result = 0
    if args.command in ("validate-youtube", "validate-all"):
        youtube_result = validate_youtube(
            registry, min(max(args.limit, 1), 25), force=args.force,
            exact_only=args.exact_only)
    if args.command in ("validate-podcasts", "validate-all"):
        podcast_result = validate_podcasts(
            registry, min(max(args.limit, 1), 25), force=args.force,
            exact_only=args.exact_only)
    return 0 if youtube_result == 0 and podcast_result == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
