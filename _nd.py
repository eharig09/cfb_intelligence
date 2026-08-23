import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("Notre Dame stat rows by season:",
      [tuple(r) for r in c.execute("select season, count(*) from player_season_stats where team='Notre Dame' group by 1 order by 1")])
print("\nND QBs in 2025 stats:")
for r in c.execute("""select distinct player_id, player, position from player_season_stats
    where team='Notre Dame' and season=2025 and category='passing'"""):
    print("  ", dict(r))
print("\nany stats row whose player contains 'Carr' league-wide (2025):")
for r in c.execute("""select distinct player, team from player_season_stats
    where season=2025 and player like '%Carr%' limit 8"""):
    print("  ", dict(r))
