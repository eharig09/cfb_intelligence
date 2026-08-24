from __future__ import annotations
import argparse, json, os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv
from sports_aggregator.social.bluesky import BlueskyIdentityClient
from sports_aggregator.social.registry import SourceRegistry
from sports_aggregator.social.seeds import CFB_BLUESKY_SEEDS
from sports_aggregator.social.reddit import RedditCommunityClient
from sports_aggregator.social.unified import UnifiedSourceRegistry, migrate_bluesky_sources
from sports_aggregator.social.local_sources import import_source_graph
from sports_aggregator.social.team_reddit import load_registry, register
from sports_aggregator.cfb.repository import CFBRepository

LOCAL_REGISTRY = Path("data/local_sources/cfb_local_source_registry.json")

def _import_local(unified, database):
    if not LOCAL_REGISTRY.exists(): return {"entities": 0, "endpoints": 0}
    return import_source_graph(json.loads(LOCAL_REGISTRY.read_text(encoding="utf-8")), database)

def _import_team_reddit(unified, database):
    entries=load_registry()
    if not entries: return {"registered": 0, "promoted": 0}
    registered=register(CFBRepository(database),entries)
    promoted=unified.seed_team_reddit_communities(entries)
    return {"registered": registered, "promoted": promoted}

def main(argv=None):
    load_dotenv(); p=argparse.ArgumentParser(); p.add_argument('command',choices=('seed','resolve','status','prepare','validate-reddit','unified-status')); p.add_argument('--force',action='store_true'); a=p.parse_args(argv)
    database=os.getenv('CFB_DATABASE_PATH','instance/cfb.sqlite3')
    registry=SourceRegistry(database); unified=UnifiedSourceRegistry(database)
    if a.command=='seed':
        seeded=registry.seed(CFB_BLUESKY_SEEDS); migrated=migrate_bluesky_sources(database)
        reddit=unified.seed_reddit_communities(); candidates=unified.seed_media_candidates()
        configured=unified.seed_configured_endpoints(); relationships=unified.infer_organization_relationships()
        local=_import_local(unified,database); team_reddit=_import_team_reddit(unified,database)
        print(f"bluesky_seeded={seeded} entities_migrated={migrated} reddit_seeded={reddit} team_reddit_registered={team_reddit['registered']} team_reddit_promoted={team_reddit['promoted']} media_candidates={candidates} configured_endpoints={configured} relationships_added={relationships} local_entities={local['entities']} local_endpoints={local['endpoints']}"); return 0
    if a.command=='prepare':
        migrated=migrate_bluesky_sources(database); reddit=unified.seed_reddit_communities()
        candidates=unified.seed_media_candidates(); configured=unified.seed_configured_endpoints()
        relationships=unified.infer_organization_relationships()
        local=_import_local(unified,database); team_reddit=_import_team_reddit(unified,database)
        print(f"entities_migrated={migrated} reddit_seeded={reddit} team_reddit_registered={team_reddit['registered']} team_reddit_promoted={team_reddit['promoted']} media_candidates={candidates} configured_endpoints={configured} relationships_added={relationships} local_entities={local['entities']} local_endpoints={local['endpoints']}"); return 0
    if a.command=='status':
        s=registry.status(); print(f"sources={s['count']} verified={s['verified']} failed_or_unresolved={s['failed']}"); return 0
    if a.command=='unified-status':
        s=unified.status(); print(json.dumps({k:v for k,v in s.items() if k!='entities'},indent=2)); return 0
    if a.command=='validate-reddit':
        team_reddit=_import_team_reddit(unified,database)
        print(f"team_reddit_registered={team_reddit['registered']} team_reddit_promoted={team_reddit['promoted']}")
        endpoints=unified.endpoints_by_platform('reddit'); client=RedditCommunityClient(); results=[]
        for endpoint in endpoints:
            result=client.resolve(endpoint['handle']); unified.store_endpoint_resolution(result)
            results.append(result); print(f"{endpoint['handle']}: {result.status}")
        return 0 if all(result.status=='verified' for result in results) else 1
    handles=registry.unresolved_handles(a.force); client=BlueskyIdentityClient()
    with ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(client.resolve,handles))
    for result in results: registry.store_resolution(result); print(f"{result.requested_handle}: {result.status}")
    migrate_bluesky_sources(database); unified.seed_configured_endpoints()
    return 0 if all(r.status=='verified' for r in results) else 1
if __name__=='__main__': raise SystemExit(main())
