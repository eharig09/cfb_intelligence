import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("story sizes:", [tuple(x) for x in c.execute(
    "select n, count(*) from (select story_id, count(*) n from story_items group by 1) group by 1 order by 1")])
print("methods:", [tuple(x) for x in c.execute(
    "select clustering_method, count(*) from stories group by 1 order by 2 desc")])
total = c.execute("select count(*) from stories").fetchone()[0]
multi = c.execute("select count(*) from (select story_id from story_items group by 1 having count(*)>1)").fetchone()[0]
print(f"multi-source: {multi}/{total} = {100*multi/max(total,1):.0f}%")
print("\nlargest clusters:")
for r in c.execute("""select s.story_id, s.clustering_method, s.headline_canonical, count(*) n
    from stories s join story_items si using(story_id) group by 1 order by n desc limit 4"""):
    print(f"  n={r['n']:2d} {r['clustering_method']:18s} {r['headline_canonical'][:56]!r}")
    for m in c.execute("""select substr(coalesce(nullif(i.title,''), i.body_text),1,50) t, i.platform
        from story_items si join content_items i using(content_id) where si.story_id=? limit 4""", (r["story_id"],)):
        print(f"        {m['platform']:8s} {m['t']!r}")
