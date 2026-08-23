import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("=== all remaining unscoped links ===")
for r in c.execute("""select p.first_name||' '||p.last_name name, p.team, i.platform,
    substr(i.title||' '||i.body_text,1,95) txt
    from content_players cp join players p on p.player_id=cp.player_id and p.season=cp.season
    join content_items i using(content_id) where cp.method='exact_full_name_unscoped'
    order by p.last_name"""):
    print(f"  {r['name'][:21]:21s} {r['team'][:13]:13s} {' '.join(r['txt'].split())[:62]!r}")
