import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sports_aggregator.cfb.models import normalize_alias
for name in ("CJ Carr", "C.J. Carr", "C.J Carr", "Cj Carr"):
    print(f"{name!r:14s} -> {normalize_alias(name)!r}")
c = sqlite3.connect("instance/cfb.sqlite3"); c.row_factory = sqlite3.Row
print("\nroster rows matching Carr at Notre Dame:")
for r in c.execute("""select season, player_id, first_name, last_name, normalized_name, team
    from players where last_name like 'Carr%' and team='Notre Dame' order by season"""):
    print("  ", dict(r))
print("\nstat rows for that player_id:")
pid = c.execute("select player_id from players where last_name='Carr' and team='Notre Dame' limit 1").fetchone()
if pid:
    rows = c.execute("select season, count(*) from player_season_stats where player_id=? group by 1", (pid[0],)).fetchall()
    print("  ", rows)
print("\nstat rows with player name like C%J% Carr:")
for r in c.execute("""select distinct season, player_id, player, team from player_season_stats
    where player like '%Carr' and team='Notre Dame'"""):
    print("  ", dict(r))
