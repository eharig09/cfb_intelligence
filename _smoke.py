import sqlite3
from app import create_app
app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False, "CFB_DEFAULT_SEASON": 2026})
client = app.test_client()
c = sqlite3.connect("instance/cfb.sqlite3")
team = c.execute("select team_id from teams where school='Georgia'").fetchone()[0]
game = c.execute("select game_id from games where season=2026 and home_team='Georgia' limit 1").fetchone()[0]
player = c.execute("select player_id from players where season=2026 limit 1").fetchone()[0]
paths = ["/", "/college-football/", "/college-football/conferences/sec/",
         "/college-football/draft/", "/college-football/admin/links/",
         "/college-football/admin/links/?kind=team",
         "/college-football/admin/links/?method=exact_full_name_unscoped",
         f"/college-football/teams/{team}/", f"/college-football/games/{game}/",
         f"/college-football/players/{player}/",
         "/college-football/admin/sources/", "/college-football/admin/source-graph/",
         "/api/v1/cfb/status", f"/api/v1/cfb/teams/{team}", f"/api/v1/cfb/games/{game}/preview",
         f"/api/v1/cfb/games/{game}/matchups", f"/api/v1/cfb/games/{game}/player-matchups",
         "/api/v1/cfb/developments", "/api/v1/cfb/links",
         "/api/v1/cfb/draft/board", "/api/v1/cfb/draft/consensus", "/api/v1/cfb/draft/reconcile",
         f"/api/v1/cfb/players/{player}", "/api/v1/cfb/conferences/sec",
         "/api/v1/cfb/games-to-watch", "/api/v1/cfb/rankings", "/api/v1/cfb/content"]
bad = [(p, client.get(p).status_code) for p in paths if client.get(p).status_code != 200]
print(f"{len(paths)-len(bad)}/{len(paths)} routes ok", bad)
