"""CLI for validating and promoting curated YouTube and podcast candidates."""

from __future__ import annotations

import argparse
import json
import os

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


def _classes(candidate: dict) -> set[str]:
    return {
        item.strip()
        for item in (candidate.get("proposed_classes") or "").split(",")
        if item.strip()
    }


def validate_youtube(registry: MediaRegistry, limit: int) -> int:
    client = YouTubeDataClient()
    promoted = reviewed = errors = 0
    for candidate in registry.pending_candidates():
        if "YOUTUBE_SHOW" not in _classes(candidate):
            continue
        try:
            channels = client.search_channels(candidate["name"], max_results=limit)
            matches = [score_channel(candidate["name"], channel) for channel in channels]
            registry.record_matches(candidate["candidate_id"], "youtube", matches)
            promotable = sorted(
                (match for match in matches if match["promotable"]),
                key=lambda match: match["score"], reverse=True,
            )
            if promotable:
                best = promotable[0]
                registry.promote_channel(candidate, best)
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
                print(f"youtube {candidate['name']}: REVIEW ({note})")
        except Exception as exc:
            errors += 1
            registry.mark_needs_review(candidate["candidate_id"], f"YouTube validation error: {exc}")
            print(f"youtube {candidate['name']}: ERROR ({exc})")
    print(f"youtube promoted={promoted} review={reviewed} errors={errors}")
    return 0 if errors == 0 else 1


def validate_podcasts(registry: MediaRegistry, limit: int) -> int:
    directory = PodcastDirectoryClient()
    rss = PodcastRSSClient()
    promoted = reviewed = errors = 0
    for candidate in registry.pending_candidates():
        if "PODCAST" not in _classes(candidate):
            continue
        try:
            shows = directory.search(candidate["name"], limit=limit)
            matches = [score_podcast(candidate["name"], show) for show in shows]
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
                promoted += 1
                print(
                    f"podcast {candidate['name']}: PROMOTED -> {feed_title} "
                    f"(score={chosen['score']:.3f})"
                )
            else:
                reviewed += 1
                best = max(matches, key=lambda match: match["score"], default=None)
                note = "no podcast directory results"
                if best:
                    blockers = "; ".join(best["blockers"]) or "feed identity check failed"
                    note = f"best podcast match {best['title']} score={best['score']:.3f}: {blockers}"
                registry.mark_needs_review(candidate["candidate_id"], note)
                print(f"podcast {candidate['name']}: REVIEW ({note})")
        except Exception as exc:
            errors += 1
            registry.mark_needs_review(candidate["candidate_id"], f"Podcast validation error: {exc}")
            print(f"podcast {candidate['name']}: ERROR ({exc})")
    print(f"podcast promoted={promoted} review={reviewed} errors={errors}")
    return 0 if errors == 0 else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Validate curated CFB media candidates")
    parser.add_argument(
        "command",
        choices=("validate-youtube", "validate-podcasts", "validate-all", "status"),
    )
    parser.add_argument("--limit", type=int, default=5, help="Directory/search results per candidate")
    args = parser.parse_args(argv)

    database = os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3")
    registry = MediaRegistry(database)

    if args.command == "status":
        status = registry.status()
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
        youtube_result = validate_youtube(registry, min(max(args.limit, 1), 25))
    if args.command in ("validate-podcasts", "validate-all"):
        podcast_result = validate_podcasts(registry, min(max(args.limit, 1), 25))
    return 0 if youtube_result == 0 and podcast_result == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
