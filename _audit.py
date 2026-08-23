import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("total player links:", c.execute("select count(*) from content_players").fetchone()[0])
print("by method:", [tuple(r) for r in c.execute(
    "select method, count(*) from content_players group by 1 order by 2 desc")])
print()
for method in ("exact_full_name_on_resolved_team", "exact_full_name_unscoped"):
    print(f"===== {method} =====")
    for r in c.execute("""
        select p.first_name||' '||p.last_name name, p.team, p.position,
               i.platform, e.name src, substr(i.title||' '||i.body_text,1,120) txt
        from content_players cp
        join players p on p.player_id=cp.player_id and p.season=cp.season
        join content_items i using(content_id)
        left join source_entities e using(source_entity_id)
        where cp.method=? order by random() limit 8""", (method,)):
        print(f"  [{r['name']} · {r['team']} {r['position'] or ''}] <- {r['platform']}/{(r['src'] or '?')[:18]}")
        print(f"      {' '.join(r['txt'].split())[:104]!r}")
    print()
