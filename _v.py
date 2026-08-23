import sys, io, re, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from app import create_app
app = create_app({"TESTING": True, "REGISTER_LEGACY_DASHBOARDS": False, "CFB_DEFAULT_SEASON": 2026})
c = sqlite3.connect("instance/cfb.sqlite3")
gid = c.execute("""select g.game_id from games g where g.season=2026
    and g.home_team in ('Notre Dame','Georgia') limit 1""").fetchone()[0]
b = app.test_client().get(f"/college-football/games/{gid}/").get_data(as_text=True)
print("status ok, sections:", re.findall(r"<h2>([^<]+)</h2>", b))
for cap in ("Individual matchups", "returning", "departed"):
    m = re.search(r'<strong>[^<]*' + cap + r'[^<]*</strong>.*?</table>', b, re.S)
    if m:
        print(f"\n-- {cap} --")
        print(" ".join(re.sub(r"<[^>]+>", " ", m.group(0)).split())[:280])
