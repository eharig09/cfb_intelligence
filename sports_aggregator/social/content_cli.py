"""Background-style CFB social ingestion command."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from sports_aggregator.social.bluesky import BlueskyIdentityClient
from sports_aggregator.social.media import MediaRegistry
from sports_aggregator.social.reddit import RedditContentClient
from sports_aggregator.social.content import ContentRepository
from sports_aggregator.social.stories import StoryRepository
from sports_aggregator.catalog import get_league
from sports_aggregator.service import build_default_service


def main(argv=None) -> int:
    load_dotenv(); parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("ingest","ingest-reddit","ingest-youtube",
                                          "ingest-podcasts","ingest-reporting",
                                          "ingest-local-reporting",
                                          "prune-local-non-football",
                                          "retag","roles","score","status","cluster",
                                          "review-export","review-import","review-report")); parser.add_argument("--season",type=int,default=datetime.now().year)
    parser.add_argument("--limit",type=int,default=15)
    parser.add_argument("--input")
    parser.add_argument("--output",default="instance/cfb_content_review.csv")
    parser.add_argument("--reviewer",default="local")
    parser.add_argument("--review-mode",choices=("triage","stratified"),default="triage")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    repository=ContentRepository(os.getenv("CFB_DATABASE_PATH","instance/cfb.sqlite3"))
    if args.command.startswith("review-"):
        from sports_aggregator.social.review import ContentReviewRepository
        reviews = ContentReviewRepository(repository.path)
        if args.command == "review-export":
            report = reviews.export_csv(Path(args.output), limit=args.limit,
                                        reviewer=args.reviewer, mode=args.review_mode)
        elif args.command == "review-import":
            if not args.input:
                parser.error("review-import requires --input")
            report = reviews.import_csv(Path(args.input), reviewer=args.reviewer)
        else:
            report = reviews.report(reviewer=args.reviewer)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command=="retag":
        report=repository.retag(args.season)
        print(" ".join(f"{key}={value}" for key,value in report.items()))
        print(" ".join(f"{key}={value}" for key,value in repository.rescore().items()))
        return 0
    if args.command=="prune-local-non-football":
        report=repository.prune_local_non_football(dry_run=args.dry_run)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command=="roles":
        report=repository.redetermine_roles()
        print(" ".join(f"{key}={value}" for key,value in report.items()))
        return 0
    if args.command=="score":
        print(" ".join(f"{key}={value}" for key,value in repository.rescore().items()))
        return 0
    if args.command=="cluster":
        report=StoryRepository(repository.path).rebuild(); print(" ".join(f"{key}={value}" for key,value in report.items()))
        return 0
    if args.command=="status":
        report = repository.summary()
        report["recent_items_returned"] = len(repository.recent(min(max(args.limit, 1), 100)))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command=="ingest-reddit":
        from sports_aggregator.social.team_reddit import load_registry
        from sports_aggregator.social.unified import UnifiedSourceRegistry
        UnifiedSourceRegistry(repository.path).seed_team_reddit_communities(load_registry())
        endpoints=repository.reddit_endpoints()
        if not endpoints:
            print("no verified subreddit endpoints"); return 1
        client=RedditContentClient(); seen=stored=succeeded=0; errors=[]
        started=datetime.now(timezone.utc).isoformat()
        for endpoint in endpoints:
            try:
                submissions=client.submissions(endpoint["handle"],limit=args.limit)
                succeeded+=1; seen+=len(submissions)
                stored+=sum(repository.store_reddit_submission(endpoint,item,args.season) is not None
                            for item in submissions)
            except Exception as exc:
                errors.append({"endpoint":endpoint["endpoint_key"],"error":str(exc)})
        repository.record_run(started,datetime.now(timezone.utc).isoformat(),
                              len(endpoints),succeeded,seen,stored,errors,platform="reddit")
        print(f"subreddits={len(endpoints)} succeeded={succeeded} seen={seen} stored={stored} errors={len(errors)}")
        return 0 if not errors else 1
    if args.command=="ingest-youtube":
        from sports_aggregator.providers.youtube import YouTubeDataClient
        registry=MediaRegistry(repository.path); endpoints=registry.youtube_endpoints()
        if not endpoints:
            print("no verified YouTube endpoints; run the media validator first"); return 1
        client=YouTubeDataClient(); seen=stored=succeeded=0; errors=[]
        started=datetime.now(timezone.utc).isoformat()
        for endpoint in endpoints:
            try:
                videos=client.uploads(endpoint["platform_id"],max_results=args.limit)
                succeeded+=1; seen+=len(videos)
                stored+=sum(repository.store_youtube_video(endpoint,video,args.season) is not None
                            for video in videos)
            except Exception as exc:
                errors.append({"endpoint":endpoint["endpoint_key"],"error":str(exc)})
        repository.record_run(started,datetime.now(timezone.utc).isoformat(),
                              len(endpoints),succeeded,seen,stored,errors,platform="youtube")
        print(f"channels={len(endpoints)} succeeded={succeeded} seen={seen} stored={stored} errors={len(errors)}")
        return 0 if not errors else 1
    if args.command=="ingest-podcasts":
        from sports_aggregator.providers.podcast import PodcastRSSClient
        registry=MediaRegistry(repository.path); endpoints=registry.podcast_endpoints()
        if not endpoints:
            print("no verified podcast endpoints; run the media validator first"); return 1
        client=PodcastRSSClient(); seen=stored=succeeded=0; errors=[]
        started=datetime.now(timezone.utc).isoformat()
        for endpoint in endpoints:
            try:
                episodes=client.episodes(endpoint["platform_id"],limit=args.limit)
                succeeded+=1; seen+=len(episodes)
                stored+=sum(repository.store_podcast_episode(endpoint,episode,args.season) is not None
                            for episode in episodes)
            except Exception as exc:
                errors.append({"endpoint":endpoint["endpoint_key"],"error":str(exc)})
        repository.record_run(started,datetime.now(timezone.utc).isoformat(),
                              len(endpoints),succeeded,seen,stored,errors,platform="podcast")
        print(f"feeds={len(endpoints)} succeeded={succeeded} seen={seen} stored={stored} errors={len(errors)}")
        return 0 if not errors else 1
    if args.command=="ingest-reporting":
        started=datetime.now(timezone.utc).isoformat()
        result=build_default_service().aggregate(get_league("college-football"),force_refresh=True)
        stored=sum(repository.store_article(article,args.season) is not None for article in result.articles)
        errors=[{"source": error.source, "error": error.message} for error in result.errors]
        repository.record_run(
            started,datetime.now(timezone.utc).isoformat(),len(result.league.feeds),
            len(result.league.feeds)-len(errors),len(result.articles),stored,errors,
            platform="rss",
        )
        clustered=StoryRepository(repository.path).rebuild()
        print(f"articles={len(result.articles)} stored={stored} errors={len(result.errors)} stories={clustered['stories']}")
        return 0 if not result.errors else 1
    if args.command=="ingest-local-reporting":
        from sports_aggregator.models import FeedConfig
        from sports_aggregator.providers.rss import RSSNewsProvider
        from sports_aggregator.social.local_sources import article_matches_team, _publisher_id
        registry_path=Path("data/local_sources/cfb_local_source_registry.json")
        if not registry_path.exists():
            print("local source registry is missing; run local_sources_cli research"); return 1
        registry=json.loads(registry_path.read_text(encoding="utf-8")); tasks=[]
        for team in registry["teams"].values():
            for source in team["sources"]:
                publisher_id=_publisher_id(source["domain"])
                tasks.append((team,source,FeedConfig(
                    name=source["name"],url=source["google_news_rss"],
                    max_articles=args.limit,source_type="local_reporting",
                    reliability=4 if source["confidence"]=="high" else 3,
                    source_entity_key=f"local-publisher:{publisher_id}",
                    source_endpoint_key=f"rss:google-news:{publisher_id}:{team['team_id']}",
                )))
        errors=[]; succeeded=0; combined={}; team_ids={}
        started=datetime.now(timezone.utc).isoformat()
        def fetch_local(task):
            team,source,config=task
            return team,source,RSSNewsProvider(config).fetch()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures={pool.submit(fetch_local,task):task for task in tasks}
            for future in as_completed(futures):
                team,source,_=futures[future]
                try:
                    _,_,articles=future.result(); succeeded+=1
                    # Process each completed feed immediately instead of holding
                    # every feed response in memory until the slowest one ends.
                    for article in articles:
                        if not article_matches_team(
                            f"{article.title} {article.summary}", team,
                            publisher=article.publisher or source["name"],
                        ):
                            continue
                        combined.setdefault(article.identity,article)
                        team_ids.setdefault(article.identity,set()).add(int(team["team_id"]))
                except Exception as exc:
                    errors.append({"team":team["team"],"domain":source["domain"],"error":str(exc)})
        stored=0
        for identity,article in combined.items():
            repository.store_article(replace(article,team_ids=tuple(sorted(team_ids[identity]))),args.season)
            stored+=1
        repository.record_run(
            started,datetime.now(timezone.utc).isoformat(),len(tasks),succeeded,
            len(combined),stored,errors,platform="rss-local",
        )
        print(f"feeds={len(tasks)} succeeded={succeeded} articles={len(combined)} stored={stored} errors={len(errors)}")
        return 0 if not errors else 1
    started=datetime.now(timezone.utc).isoformat(); endpoints=repository.bluesky_endpoints()
    client=BlueskyIdentityClient(); succeeded=seen=stored=0; errors=[]
    def fetch(endpoint):
        return endpoint, client.author_feed(endpoint["platform_id"],args.limit)
    fetched=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(fetch,endpoint):endpoint for endpoint in endpoints}
        for future in as_completed(futures):
            endpoint=futures[future]
            try:
                fetched.append(future.result())
            except Exception as exc:
                errors.append({"endpoint":endpoint["endpoint_key"],"error":str(exc)})
    for endpoint,feed in fetched:
        try:
            succeeded+=1; seen+=len(feed)
            stored+=sum(repository.store_bluesky_post(endpoint,item,args.season) is not None for item in feed)
        except Exception as exc:
            errors.append({"endpoint":endpoint["endpoint_key"],"error":str(exc)})
    finished=datetime.now(timezone.utc).isoformat()
    repository.record_run(started,finished,len(endpoints),succeeded,seen,stored,errors)
    print(f"endpoints={len(endpoints)} succeeded={succeeded} seen={seen} stored={stored} errors={len(errors)}")
    return 0 if not errors else 1


if __name__=="__main__": raise SystemExit(main())
