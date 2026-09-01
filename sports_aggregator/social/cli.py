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
from sports_aggregator.social.content_cli import ENDPOINT_FAILURE_TOLERANCE

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

def _endpoint_exit(results, *, kind: str) -> int:
    """Fail only when resolution stopped working, not when one account is gone.

    A handle that has been renamed or deleted stays in the unresolved list and
    fails on every run, so treating any failure as a failed step made "degraded"
    the permanent state of a healthy refresh. A wide failure still fails: that is
    the API being down, which is worth waking up for.
    """
    total = len(results)
    if not total:
        print(f"{kind}: nothing to resolve")
        return 0
    failed = [r for r in results if r.status != "verified"]
    share = len(failed) / total
    print(f"{kind}: {total - len(failed)}/{total} verified"
          + (f", unresolved: {', '.join(r.requested_handle for r in failed[:5])}"
             f"{'...' if len(failed) > 5 else ''}" if failed else ""))
    if share > ENDPOINT_FAILURE_TOLERANCE:
        print(f"{kind}: {share:.0%} failed, above the {ENDPOINT_FAILURE_TOLERANCE:.0%}"
              " tolerance -- treating this as a failure")
        return 1
    return 0


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
        return _endpoint_exit(results, kind='reddit')
    handles=registry.unresolved_handles(a.force); client=BlueskyIdentityClient()
    with ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(client.resolve,handles))
    for result in results: registry.store_resolution(result); print(f"{result.requested_handle}: {result.status}")
    migrate_bluesky_sources(database); unified.seed_configured_endpoints()
    return _endpoint_exit(results, kind='bluesky')
if __name__=='__main__': raise SystemExit(main())
