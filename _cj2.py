import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("CJ Carr content links:")
for r in c.execute("""select cp.method, cp.confidence, substr(i.title||' '||i.body_text,1,70) t
    from content_players cp join players p on p.player_id=cp.player_id and p.season=cp.season
    join content_items i using(content_id) where p.last_name='Carr' and p.first_name='CJ'"""):
    print(f"  {r['method']} {r['confidence']} :: {' '.join(r['t'].split())[:60]!r}")
print("\nCJ Carr stat seasons:",
      [tuple(r) for r in c.execute("""select season, count(*) from player_season_stats
         where player_id=(select player_id from players where first_name='CJ' and last_name='Carr' limit 1)
         group by 1 order by 1""")])
