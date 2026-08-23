import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv; load_dotenv()
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.cfbd import CFBDClient
repo = CFBRepository("instance/cfb.sqlite3")
stored = [item["conference"] for item in repo.conferences()]
print("stored conferences:", stored)
client = CFBDClient(raw_cache_path="instance/cfbd_raw")
catalog = {i["name"]: i.get("abbreviation") for i in client.conferences(2025) if i.get("classification") == "fbs"}
print("\nCFBD fbs catalog:", catalog)
print("\nunmatched stored names:", [n for n in stored if n not in catalog])
c = sqlite3.connect("instance/cfb.sqlite3")
print("\nteams per stored conference (2026):",
      [tuple(r) for r in c.execute("select conference, count(*) from teams group by 1 order by 2 desc")])
