import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.player_matchups import player_matchups
repo = CFBRepository("instance/cfb.sqlite3")
c = sqlite3.connect("instance/cfb.sqlite3")
# Pick games where both sides have several linked, graded players.
rows = c.execute("""
    select g.game_id, g.home_team_id, g.away_team_id, g.home_team, g.away_team,
      (select count(*) from pff_players p where p.season=2025 and p.cfbd_team_id=g.home_team_id
         and p.cfbd_player_id is not null and p.interest_score>=68) h,
      (select count(*) from pff_players p where p.season=2025 and p.cfbd_team_id=g.away_team_id
         and p.cfbd_player_id is not null and p.interest_score>=68) a
    from games g where g.season=2026 order by min(h,a) desc limit 2""").fetchall()
for gid, home, away, hname, aname, h, a in rows:
    print(f"\n=== {aname} at {hname} (linked graded: {a}/{h}) ===")
    for m in player_matchups(repo, home, away):
        at, df = m["attacker"], m["defender"]
        star = " *" if m["prospect_count"] else "  "
        print(f" {star}{m['interest']:5.1f} {m['label']:24s} {at['player_name'][:19]:19s}({at['position']}) vs {df['player_name'][:19]:19s}({df['position']})")
        print(f"          {' · '.join(m['reasons'])}")
