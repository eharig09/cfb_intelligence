import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sports_aggregator.cfb.repository import CFBRepository
from contextlib import closing
repo = CFBRepository("instance/cfb.sqlite3"); repo.initialize()
home, away = 84, 249
with closing(repo._connect()) as conn:
    rows = [dict(r) for r in conn.execute(
        """SELECT p.pff_player_id,p.player_name,p.position,p.interest_score,
           p.cfbd_player_id,p.cfbd_team_id,t.school
           FROM pff_players p JOIN teams t ON t.team_id=p.cfbd_team_id
           LEFT JOIN players r ON r.player_id=p.cfbd_player_id AND r.season=?
           WHERE p.season=? AND p.cfbd_team_id IN (?,?) AND p.interest_score IS NOT NULL""",
        (2026, 2025, home, away))]
print("rows total:", len(rows))
linked = [r for r in rows if r["cfbd_player_id"]]
print("with cfbd_player_id:", len(linked))
for tid in (home, away):
    for pos in ("WR", "CB", "ED", "T"):
        got = [r for r in linked if r["cfbd_team_id"] == tid and r["position"] == pos
               and (r["interest_score"] or 0) >= 68]
        print(f"  team {tid} {pos}: {len(got)}", [f"{g['player_name']} {g['interest_score']:.0f}" for g in got[:2]])
