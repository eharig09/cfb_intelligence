"""CLI for play-by-play backfill and in-house model development.

Examples:
  python -m sports_aggregator.cfb.pbp_cli backfill --from-year 2022 --to-year 2025
  python -m sports_aggregator.cfb.pbp_cli fit-edp --from-year 2022 --to-year 2025
  python -m sports_aggregator.cfb.pbp_cli fit-wp --from-year 2022 --to-year 2025
  python -m sports_aggregator.cfb.pbp_cli rebuild-values --from-year 2022 --to-year 2026
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError, FINISHED_WEEK_TTL
from sports_aggregator.cfb.expected_points import fit_model as fit_edp, score_plays as score_edp
from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.play_by_play import replace_week_plays, derive_week
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.win_probability import fit_model as fit_wp, score_plays as score_wp


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="CFB play-by-play analytics")
    p.add_argument("command",choices=("backfill","derive","fit-edp","score-edp","fit-wp","score-wp","rebuild-values","pace"))
    p.add_argument("--year",type=int,default=datetime.now().year)
    p.add_argument("--from-year",type=int,default=None)
    p.add_argument("--to-year",type=int,default=None)
    p.add_argument("--week",type=int,default=None)
    p.add_argument("--game-id",type=int,default=None)
    p.add_argument("--force",action="store_true")
    p.add_argument("--database",default=None)
    return p


def _years(args) -> tuple[int,int]:
    first=args.from_year or args.year; last=args.to_year or args.year
    if first>last: raise ValueError("--from-year must not be after --to-year")
    return first,last


def main(argv:list[str]|None=None,*,client=None)->int:
    load_dotenv(); args=parser().parse_args(argv)
    repository=CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH","instance/cfb.sqlite3"))
    first,last=_years(args)

    if args.command=="pace":
        if not args.game_id: raise SystemExit("pace requires --game-id")
        print(json.dumps(game_pace_summary(repository,args.game_id),indent=2,default=str)); return 0

    if args.command=="derive":
        total={"plays":0,"drives":0}
        for year in range(first,last+1):
            weeks=[args.week] if args.week is not None else repository.completed_weeks(year)
            for week in weeks:
                result=derive_week(repository,season=year,week=int(week))
                print(f"{year} week {week}: plays={result['plays']} drives={result['drives']}")
                total["plays"]+=result["plays"]; total["drives"]+=result["drives"]
        print(json.dumps(total)); return 0

    if args.command=="fit-edp":
        print(json.dumps(fit_edp(repository,from_season=first,to_season=last),indent=2)); return 0
    if args.command=="score-edp":
        print(json.dumps(score_edp(repository,from_season=first,to_season=last),indent=2)); return 0
    if args.command=="fit-wp":
        print(json.dumps(fit_wp(repository,from_season=first,to_season=last),indent=2)); return 0
    if args.command=="score-wp":
        print(json.dumps(score_wp(repository,from_season=first,to_season=last),indent=2)); return 0
    if args.command=="rebuild-values":
        edp=score_edp(repository,from_season=first,to_season=last)
        wp=score_wp(repository,from_season=first,to_season=last)
        print(json.dumps({"edp":edp,"wp":wp},indent=2)); return 0

    client=client or CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH","instance/cfbd_raw"))
    if not client.configured: raise CFBDConfigurationError("CFBD_API_KEY is required for PBP backfill")
    failures=[]; total=0
    for year in range(first,last+1):
        weeks=[args.week] if args.week is not None else repository.completed_weeks(year)
        if not weeks:
            print(f"{year}: no completed weeks stored; sync game history first")
            continue
        for week in weeks:
            try:
                raw=client.get("/plays",{"year":year,"week":int(week),"seasonType":"both","classification":"fbs"},
                               cache_ttl_seconds=FINISHED_WEEK_TTL,force=args.force)
                count=replace_week_plays(repository,raw,season=year,week=int(week)); total+=count
                print(f"{year} week {week}: {count} plays")
            except Exception as exc:
                failures.append(f"{year} week {week}"); print(f"{year} week {week}: failed ({exc})")
    print(f"PBP backfill complete: {total} plays, {len(failures)} failures")
    return 1 if failures else 0


if __name__=="__main__":
    raise SystemExit(main())
