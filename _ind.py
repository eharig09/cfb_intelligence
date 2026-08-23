import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv; load_dotenv()
from sports_aggregator.cfb.cfbd import CFBDClient
client = CFBDClient(raw_cache_path="instance/cfbd_raw")
for conf in ("Ind", "ACC"):
    rows = client.player_season_stats(2025, conf, force=True)
    teams = sorted({r.get("team") for r in rows})
    print(f"{conf}: {len(rows)} rows, teams={teams[:8]}")
