"""Named college-football rivalry context for schedules and game previews.

The registry is intentionally small, local, and slow-changing.  Entries are
seeded from Wikipedia's FBS rivalry list, which only includes series with a
rivalry/trophy article; that makes it a useful editorial threshold for calling
a matchup a rivalry rather than treating every old series as special.

The web layer never calls Wikipedia at request time.  This keeps team and game
pages deterministic and fast; the seed can be refreshed periodically without
changing the presentation contract below.
"""

from __future__ import annotations

from typing import Any, Iterable
import re
import unicodedata


WIKIPEDIA_SOURCE = (
    "https://en.wikipedia.org/wiki/"
    "List_of_NCAA_college_football_rivalry_games"
)


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    aliases = {
        "miami oh": "miami ohio",
        "miami ohio": "miami ohio",
        "miami fl": "miami",
        "ul monroe": "louisiana monroe",
        "ulm": "louisiana monroe",
        "louisiana monroe": "louisiana monroe",
        "louisiana lafayette": "louisiana",
        "san jose st": "san jose state",
        "nc state": "north carolina state",
        "north carolina st": "north carolina state",
        "app state": "appalachian state",
        "southern miss": "southern mississippi",
        "ole miss": "ole miss",
        "mississippi": "ole miss",
        "ut san antonio": "utsa",
        "texas san antonio": "utsa",
        "florida atlantic": "fau",
        "florida international": "fiu",
        "connecticut": "uconn",
        "massachusetts": "umass",
    }
    return aliases.get(text, text)


def _seed(team1: str, team2: str, name: str, trophy: str | None = None,
          first_year: int | None = None) -> dict[str, Any]:
    return {
        "teams": (team1, team2),
        "name": name,
        "trophy": trophy,
        "first_year": first_year,
        "source": "Wikipedia",
        "source_url": WIKIPEDIA_SOURCE,
    }


# High-value current/recent FBS rivalries.  The source page contains many more
# dormant historical series; this seed favors matchups likely to appear on a
# modern schedule while preserving their article/trophy name when one exists.
_SEEDS = [
    _seed("Middle Tennessee", "Western Kentucky", "100 Miles of Hate", first_year=1914),
    _seed("Air Force", "Colorado State", "Air Force–Colorado State", "Ram–Falcon Trophy", 1957),
    _seed("Air Force", "Hawaii", "Air Force–Hawaii", "Kuter Trophy", 1966),
    _seed("Akron", "Kent State", "Akron–Kent State", "Wagon Wheel", 1923),
    _seed("Washington", "Washington State", "Apple Cup", "Apple Cup Trophy", 1900),
    _seed("Arkansas", "LSU", "Arkansas–LSU", "Golden Boot", 1901),
    _seed("Arkansas", "Missouri", "Battle Line Rivalry", "Battle Line Trophy", 1906),
    _seed("Arkansas", "Texas", "Arkansas–Texas", first_year=1894),
    _seed("Arkansas", "Texas A&M", "Arkansas–Texas A&M", "Southwest Classic Trophy", 1903),
    _seed("Army", "Navy", "Army–Navy Game", first_year=1890),
    _seed("Pittsburgh", "West Virginia", "Backyard Brawl", first_year=1895),
    _seed("Ball State", "Northern Illinois", "Ball State–Northern Illinois", "Bronze Stalk Trophy", 1941),
    _seed("Nevada", "UNLV", "Battle for Nevada", "Fremont Cannon", 1969),
    _seed("Cincinnati", "Miami (OH)", "Battle for the Bell", "Victory Bell", 1888),
    _seed("Marshall", "Ohio", "Battle for the Bell", "The Bell", 1905),
    _seed("South Alabama", "Troy", "Battle for the Belt", "The Belt", 2012),
    _seed("Memphis", "UAB", "Battle for the Bones", "The Bones", 1997),
    _seed("SMU", "TCU", "Battle for the Iron Skillet", "Iron Skillet", 1915),
    _seed("Michigan State", "Penn State", "Battle for the Land Grant", "Land Grant Trophy", 1914),
    _seed("New Mexico State", "UTEP", "Battle of I-10", "Silver Spade", 1914),
    _seed("Miami (OH)", "Ohio", "Battle of the Bricks", first_year=1908),
    _seed("Louisiana", "Louisiana-Monroe", "Battle on the Bayou", "Wooden Boot", 1951),
    _seed("Baylor", "TCU", "Bluebonnet Battle", "Bluebonnet Shield", 1899),
    _seed("Oklahoma", "Oklahoma State", "Bedlam Series", "Bedlam Bell", 1904),
    _seed("California", "Stanford", "Big Game", "The Axe", 1892),
    _seed("Boise State", "Fresno State", "Boise State–Fresno State", "Milk Can", 1977),
    _seed("Colorado State", "Wyoming", "Border War", "Bronze Boot", 1899),
    _seed("Kansas", "Missouri", "Border War", "Lamar Hunt Trophy", 1891),
    _seed("Bowling Green", "Toledo", "Bowling Green–Toledo", "Battle of I-75 Trophy", 1919),
    _seed("Central Michigan", "Eastern Michigan", "Central Michigan–Eastern Michigan", first_year=1902),
    _seed("Central Michigan", "Western Michigan", "Central Michigan–Western Michigan", "Victory Cannon", 1907),
    _seed("Cincinnati", "Louisville", "Cincinnati–Louisville", "Keg of Nails", 1929),
    _seed("Georgia", "Georgia Tech", "Clean, Old-Fashioned Hate", "Governor's Cup", 1893),
    _seed("Appalachian State", "Georgia Southern", "Deeper than Hate", first_year=1932),
    _seed("Auburn", "Georgia", "Deep South's Oldest Rivalry", first_year=1892),
    _seed("Arizona", "Arizona State", "Duel in the Desert", "Territorial Cup", 1899),
    _seed("Duke", "North Carolina", "Duke–North Carolina", "Victory Bell", 1888),
    _seed("Mississippi State", "Ole Miss", "Egg Bowl", "Golden Egg Trophy", 1901),
    _seed("Iowa State", "Kansas State", "Farmageddon", first_year=1917),
    _seed("Florida", "Florida State", "Florida–Florida State", "Florida Cup", 1958),
    _seed("Florida", "Georgia", "Florida–Georgia", "Okefenokee Oar", 1915),
    _seed("Florida", "Miami (FL)", "Florida–Miami", "Florida Cup", 1938),
    _seed("Florida State", "Miami (FL)", "Florida State–Miami", "Florida Cup", 1951),
    _seed("Boston College", "Notre Dame", "Frank Leahy Memorial Bowl", "Ireland Trophy", 1975),
    _seed("Fresno State", "Hawaii", "Fresno State–Hawaii", "Golden Screwdriver", 1938),
    _seed("Fresno State", "San Jose State", "Fresno State–San Jose State", "Oil Can", 1923),
    _seed("BYU", "Utah", "Holy War", first_year=1896),
    _seed("Houston", "Rice", "Houston–Rice", "Bayou Bucket", 1971),
    _seed("Texas State", "UTSA", "I-35 Rivalry", first_year=2012),
    _seed("Illinois", "Northwestern", "Illinois–Northwestern", "Land of Lincoln Trophy", 1892),
    _seed("Illinois", "Ohio State", "Illinois–Ohio State", "Illibuck", 1902),
    _seed("Illinois", "Purdue", "Illinois–Purdue", "Purdue Cannon", 1890),
    _seed("Indiana", "Michigan State", "Indiana–Michigan State", "Old Brass Spittoon", 1922),
    _seed("Indiana", "Purdue", "Indiana–Purdue", "Old Oaken Bucket", 1891),
    _seed("Iowa", "Iowa State", "Iowa–Iowa State", "Cy-Hawk Trophy", 1894),
    _seed("Iowa", "Minnesota", "Iowa–Minnesota", "Floyd of Rosedale", 1891),
    _seed("Iowa", "Nebraska", "Iowa–Nebraska", "Heroes Trophy", 1891),
    _seed("Iowa", "Wisconsin", "Iowa–Wisconsin", "Heartland Trophy", 1894),
    _seed("Alabama", "Auburn", "Iron Bowl", "James E. Foy Sportsmanship Trophy", 1893),
    _seed("Kentucky", "Louisville", "Kentucky–Louisville", "Governor's Cup", 1912),
    _seed("Texas", "Texas A&M", "Lone Star Showdown", first_year=1894),
    _seed("LSU", "Ole Miss", "Magnolia Bowl", "Magnolia Bowl Trophy", 1894),
    _seed("Michigan", "Michigan State", "Michigan–Michigan State", "Paul Bunyan Trophy", 1898),
    _seed("Michigan", "Minnesota", "Michigan–Minnesota", "Little Brown Jug", 1892),
    _seed("Michigan", "Notre Dame", "Michigan–Notre Dame", first_year=1887),
    _seed("Minnesota", "Wisconsin", "Minnesota–Wisconsin", "Paul Bunyan's Axe", 1890),
    _seed("Navy", "Notre Dame", "Navy–Notre Dame", "Rip Miller Trophy", 1927),
    _seed("NC State", "Wake Forest", "NC State–Wake Forest", first_year=1895),
    _seed("North Carolina", "NC State", "North Carolina–NC State", first_year=1894),
    _seed("Notre Dame", "Purdue", "Notre Dame–Purdue", "Shillelagh Trophy", 1896),
    _seed("Notre Dame", "Stanford", "Notre Dame–Stanford", "Legends Trophy", 1925),
    _seed("Notre Dame", "USC", "Notre Dame–USC", "Jeweled Shillelagh", 1926),
    _seed("Oregon", "Oregon State", "Oregon–Oregon State", "Platypus Trophy", 1894),
    _seed("Oregon", "Washington", "Oregon–Washington", first_year=1900),
    _seed("Clemson", "South Carolina", "Palmetto Bowl", first_year=1896),
    _seed("Oklahoma", "Texas", "Red River Rivalry", "Golden Hat", 1900),
    _seed("New Mexico", "New Mexico State", "Rio Grande Rivalry", "The Roaster", 1894),
    _seed("Colorado", "Colorado State", "Rocky Mountain Showdown", "Centennial Cup", 1893),
    _seed("Florida Atlantic", "FIU", "Shula Bowl", "Don Shula Award", 2002),
    _seed("North Carolina", "Virginia", "South's Oldest Rivalry", first_year=1892),
    _seed("Kansas", "Kansas State", "Sunflower Showdown", "Governor's Cup", 1902),
    _seed("TCU", "Texas Tech", "TCU–Texas Tech", "Saddle Trophy", 1926),
    _seed("Clemson", "NC State", "Textile Bowl", "Textile Bowl", 1899),
    _seed("Michigan", "Ohio State", "The Game", first_year=1897),
    _seed("Alabama", "Tennessee", "Third Saturday in October", first_year=1901),
    _seed("UCLA", "USC", "UCLA–USC", "Victory Bell", 1929),
    _seed("Virginia", "Virginia Tech", "Virginia–Virginia Tech", "Commonwealth Cup", 1895),
    _seed("South Florida", "UCF", "War on I-4", "War on I-4 Trophy", 2005),
]


def _key(team1: str | None, team2: str | None) -> frozenset[str]:
    return frozenset((_norm(team1), _norm(team2)))


_BY_PAIR = {_key(*entry["teams"]): entry for entry in _SEEDS}


def rivalry_for(team1: str | None, team2: str | None) -> dict[str, Any] | None:
    """Return a detached rivalry packet for a pair of schools, if known."""
    entry = _BY_PAIR.get(_key(team1, team2))
    return dict(entry) if entry else None


def rivalries_for_team(team: str | None) -> list[dict[str, Any]]:
    """Known modern rivalry entries involving one school."""
    normalized = _norm(team)
    rows = [dict(entry) for entry in _SEEDS
            if normalized in {_norm(name) for name in entry["teams"]}]
    rows.sort(key=lambda row: (row.get("name") or "", row["teams"]))
    return rows


def annotate_game(game: dict[str, Any]) -> dict[str, Any]:
    rivalry = rivalry_for(game.get("away_team"), game.get("home_team"))
    game["rivalry"] = rivalry
    game["rivalry_name"] = rivalry.get("name") if rivalry else None
    game["rivalry_trophy"] = rivalry.get("trophy") if rivalry else None
    return game


def annotate_games(games: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [annotate_game(game) for game in games]
