from flask import Blueprint, render_template
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re
import os
from dotenv import load_dotenv

import statsapi
from dateutil import parser

import praw
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from scipy.stats import percentileofscore
from pybaseball import batting_stats, pitching_stats, statcast, playerid_lookup, statcast_pitcher
from sklearn.linear_model import LinearRegression

import seaborn as sns


load_dotenv()

reds = Blueprint("reds", __name__, template_folder="../templates")

TEAM_ID_REDS = 113  # CIN in statsapi

# Season mode:
# - "auto"  : decide based on date
# - "spring": force spring training mode
# - "regular": force regular season mode
SEASON_MODE = os.getenv("REDS_SEASON_MODE", "auto").strip().lower()

# -------------------------
# Helpers
# -------------------------

def current_season_year() -> int:
    """
    In Feb, many public stat endpoints are flaky or empty for the new season.
    Prefer last season until March/April.
    Adjust this logic if you want earlier.
    """
    now = datetime.now()
    if now.month <= 2:
        return now.year - 1
    return now.year


def safe_pybaseball_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        print(f"WARNING: {func.__name__} failed: {e}")
        return None


def ensure_static_dir():
    os.makedirs("static", exist_ok=True)


def parse_timestamp(timestamp_str):
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        try:
            return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            print(f"Failed to parse timestamp: {timestamp_str}")
            return None


def extract_links(text: str):
    url_regex = r"(https?://\S+)"
    return re.findall(url_regex, text or "")


import time
import random
import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

def get_soup(url, headers=None, timeout=15, retries=3, backoff=1.5):
    headers = headers or DEFAULT_HEADERS

    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            # Helpful debug
            if resp.status_code in (403, 429):
                print(f"[get_soup] {resp.status_code} for {url}")
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            last_err = e
            sleep_s = (backoff ** attempt) + random.random() * 0.25
            time.sleep(sleep_s)

    print(f"[get_soup] FAILED {url} after {retries} tries: {last_err}")
    return None


from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser
import re

def sort_articles(articles):
    def parse_date(date_str):
        # Always return timezone-aware UTC datetime
        if not date_str:
            return datetime.min.replace(tzinfo=timezone.utc)

        s = str(date_str).strip()

        # 1) Try robust parser first (handles RSS "Tue, 20 Feb 2026 12:34:00 GMT", ISO, etc.)
        try:
            dt = dateparser.parse(s)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt
        except Exception:
            pass

        # 2) Relative times like "3h", "2 days", "15m"
        lower = s.lower()
        match = re.match(r"^\s*(\d+)\s*(d|day|days|h|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\s*$", lower)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            now = datetime.now(timezone.utc)
            if unit in ["d", "day", "days"]:
                return now - timedelta(days=value)
            if unit in ["h", "hour", "hours"]:
                return now - timedelta(hours=value)
            if unit in ["m", "min", "mins", "minute", "minutes"]:
                return now - timedelta(minutes=value)
            if unit in ["s", "sec", "secs", "second", "seconds"]:
                return now - timedelta(seconds=value)

        # 3) If all else fails
        return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(articles, key=lambda x: parse_date(x.get("date", "")), reverse=True)


def get_season_mode(now=None) -> str:
    """
    Returns: "spring" or "regular"
    Uses env override if set.
    """
    now = now or datetime.now()

    if SEASON_MODE in ("spring", "regular"):
        return SEASON_MODE

    # AUTO heuristic:
    # Feb–Mar => spring training (covers most of it)
    # Apr–Sep => regular season
    # Oct => postseason-ish, but your app can treat it like regular for schedule/boxscore
    if now.month in (2, 3):
        return "spring"
    return "regular"


def allowed_game_types(now=None) -> set[str]:
    """
    statsapi.schedule() returns game['game_type'] (e.g., 'S' spring, 'R' regular).
    We'll filter locally based on mode.
    """
    mode = get_season_mode(now)
    if mode == "spring":
        return {"S"}  # Spring Training
    return {"R"}      # Regular Season
# -------------------------
# Routes
# -------------------------

@reds.route("/", methods=["GET"])
def home():
    """
    Robust home:
    - Works during offseason/spring training without Statcast or FanGraphs.
    - Never crashes the page if a datasource is empty or changes HTML.
    """
    ensure_static_dir()

    # Find most recent Reds game within last 7 days (may still find offseason games depending on statsapi)
    schedule_data = None
    days_back = 0
    while days_back <= 7:
        game_date_try = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        schedule_data = schedule_filtered(TEAM_ID_REDS, game_date_try, game_date_try)
        if schedule_data:
            break
        days_back += 1

    if not schedule_data:
        # If no games in last week, still render page with news/leaderboards
        print("No Reds games in the last 7 days per statsapi schedule(). Rendering without pitch mix.")
        game_date = None
        opponent = None
        df = pd.DataFrame()
        starter = None
        pitch_summary = []
        statcast_available = False
    else:
        game_date = schedule_data[0].get("game_date")
        opponent = schedule_data[0]["away_name"] if schedule_data[0]["home_id"] == TEAM_ID_REDS else schedule_data[0]["home_name"]

        # Pitch mix / starter from Statcast, if available
        df, starter, game_date, opponent = get_pitch_mix_for_last_game()
        pitch_summary = df.to_dict(orient="records") if (df is not None and not df.empty) else []
        statcast_available = bool(starter) and (df is not None and not df.empty)

    # Heatmaps only if Statcast works
    if statcast_available and game_date and starter:
        sc = safe_pybaseball_call(statcast, game_date, game_date)
        if sc is not None and not sc.empty:
            generate_heatmaps_for_pitches(sc, starter)
        else:
            print("Skipping heatmaps: no Statcast data returned.")

    # Image paths (they may or may not exist; template should handle gracefully)
    pitch_mix_img = "static/pitch_mix.png"
    velo_spin_img = "static/velo_spin.png"
    break_chart = "static/break_chart.png"
    heatmap_all_img = "static/heatmap_all.png"
    heatmap_whiffs_img = "static/heatmap_whiffs.png"
    heatmap_called_strikes_img = "static/heatmap_called_strikes.png"

    # Aggregate other data like news, skeets, etc.
    (
        news,
        skeets,
        latest_game_summary,
        latest_game_details,
        boxscore,
        game_link,
        upcoming_game_summary,
        upcoming_game_pitchers,
        reds_batting_leaderboards,
        reds_pitching_leaderboards,
        schedule,
        reddit_posts,
        hot_batters,
        cold_batters,
        hot_pitchers,
        cold_pitchers,
    ) = aggregate_news()

    return render_template(
        "reds.html",
        # statcast/pitch mix
        statcast_available=statcast_available,
        pitch_mix_img=pitch_mix_img,
        velo_spin_img=velo_spin_img,
        break_chart=break_chart,
        heatmap_all_img=heatmap_all_img,
        heatmap_whiffs_img=heatmap_whiffs_img,
        heatmap_called_strikes_img=heatmap_called_strikes_img,
        starter=starter,
        game_date=game_date,
        opponent=opponent,
        pitch_summary=pitch_summary,
        # content
        news=news,
        skeets=skeets,
        latest_game_summary=latest_game_summary,
        latest_game_details=latest_game_details,
        boxscore=boxscore,
        game_link=game_link,
        upcoming_game_summary=upcoming_game_summary,
        upcoming_game_pitchers=upcoming_game_pitchers,
        schedule=schedule,
        reddit_posts=reddit_posts,
        reds_batting_leaderboards=reds_batting_leaderboards,
        reds_pitching_leaderboards=reds_pitching_leaderboards,
        hot_batters=hot_batters,
        cold_batters=cold_batters,
        hot_pitchers=hot_pitchers,
        cold_pitchers=cold_pitchers,
    )


# -------------------------
# Stats / schedule
# -------------------------

def schedule_filtered(team_id, start_date, end_date):
    games = statsapi.schedule(team=team_id, start_date=start_date, end_date=end_date) or []
    allowed = allowed_game_types()
    return [g for g in games if (g.get("game_type") in allowed)]


def get_latest_game(team_id):
    today = datetime.now().strftime("%Y-%m-%d")

    # Wider window so spring works too
    start = f"{datetime.now().year}-02-01"  # safe for both spring + regular
    games = schedule_filtered(team_id, start, today)

    if games:
        # Most recent completed game is safer than games[-2]
        # (Spring can have split-squad / cancellations / etc.)
        completed = [g for g in games if "Final" in (g.get("status") or "") or "Completed" in (g.get("status") or "")]
        latest_game = (completed[-1] if completed else games[-1])
        game_id = latest_game["game_id"]

        game_id = latest_game["game_id"]
        game_data = statsapi.get("game", {"gamePk": game_id})

        decisions = game_data.get("liveData", {}).get("decisions", {})
        winning_pitcher = decisions.get("winner", {}).get("fullName", "Unknown")
        losing_pitcher = decisions.get("loser", {}).get("fullName", "Unknown")

        boxscore = statsapi.boxscore(game_id)

        game_summary = (
            f"{latest_game['away_name']} {latest_game['away_score']} - "
            f"{latest_game['home_name']} {latest_game['home_score']} "
            f"({latest_game['status']})"
        )
        game_details = f"Winning Pitcher: {winning_pitcher}, Losing Pitcher: {losing_pitcher}"
        game_link = f"https://www.mlb.com/gameday/{game_id}"
        return game_summary, game_details, boxscore, game_link

    return "No game data available.", "", "", ""


def get_upcoming_game(team_id):
    today = datetime.now().strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")  # next 2 weeks
    games = schedule_filtered(team_id, today, end)

    if games:
        next_game = games[0]
        game_id = next_game["game_id"]
        game_data = statsapi.get("game", {"gamePk": game_id})

        home_pitcher = game_data.get("gameData", {}).get("probablePitchers", {}).get("home", {}).get("fullName", "Unknown")
        away_pitcher = game_data.get("gameData", {}).get("probablePitchers", {}).get("away", {}).get("fullName", "Unknown")

        game_summary = f"{next_game['away_name']} @ {next_game['home_name']} - {next_game['game_datetime']} ({next_game['status']})"
        pitchers = f"Probable Pitchers: {away_pitcher} vs {home_pitcher}"
        return game_summary, pitchers

    return "No upcoming game data available.", ""


def fetch_reds_schedule(team_id=TEAM_ID_REDS):
    today = datetime.now()
    one_week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    one_week_ahead = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    past_games = schedule_filtered(team_id, one_week_ago, today.strftime("%Y-%m-%d"))
    upcoming_games = schedule_filtered(team_id, today.strftime("%Y-%m-%d"), one_week_ahead)

    schedule = []

    def format_game(game):
        game_date = game["game_datetime"]
        formatted_date = datetime.strptime(game_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%A, %B %d, %Y - %I:%M %p")
        opponent = game["away_name"] if game["home_id"] == team_id else game["home_name"]
        venue = game.get("venue_name", "Unknown Venue")
        status = game.get("status", "Unknown Status")
        game_id = game["game_id"]
        game_link = f"https://www.mlb.com/gameday/{game_id}"

        if status == "Final" or "Completed" in status or "Postponed" in status:
            game_data = statsapi.get("game", {"gamePk": game_id})
            decisions = game_data.get("liveData", {}).get("decisions", {})
            winning_pitcher = decisions.get("winner", {}).get("fullName", "Unknown")
            losing_pitcher = decisions.get("loser", {}).get("fullName", "Unknown")

            score_summary = f"{game['away_name']} {game.get('away_score', '')} - {game['home_name']} {game.get('home_score', '')}"
            pitcher_summary = f"Winning Pitcher: {winning_pitcher}, Losing Pitcher: {losing_pitcher}"
            return {
                "date": formatted_date,
                "opponent": opponent,
                "venue": venue,
                "status": "Completed",
                "score": score_summary,
                "pitcher_summary": pitcher_summary,
                "game_link": game_link,
            }

        # Upcoming
        game_data = statsapi.get("game", {"gamePk": game_id})
        home_pitcher = game_data.get("gameData", {}).get("probablePitchers", {}).get("home", {}).get("fullName", "Unknown")
        away_pitcher = game_data.get("gameData", {}).get("probablePitchers", {}).get("away", {}).get("fullName", "Unknown")

        pitcher_summary = f"Probable Pitchers: {away_pitcher} vs {home_pitcher}"

        return {
            "date": formatted_date,
            "opponent": opponent,
            "venue": venue,
            "status": "Scheduled",
            "score": "N/A",
            "pitcher_summary": pitcher_summary,
            "game_link": game_link,
        }

    for g in past_games:
        schedule.append(format_game(g))
    for g in upcoming_games:
        schedule.append(format_game(g))

    print(f"Fetched {len(schedule)} games for the Cincinnati Reds")
    return schedule


# -------------------------
# Pitch mix / Statcast
# -------------------------

def get_pitch_mix_for_last_game():
    """
    Returns: (df, starter_name, game_date, opponent)
    - If Statcast isn't available (offseason), returns (empty df, None, game_date, opponent)
    """
    ensure_static_dir()

    days_back = 1
    schedule_data = None

    while days_back <= 7:
        game_date = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        schedule_data = statsapi.schedule(start_date=game_date, end_date=game_date, team=TEAM_ID_REDS)
        if schedule_data:
            break
        days_back += 1

    if not schedule_data:
        print("No Reds games in the last 7 days.")
        return pd.DataFrame(), None, None, None

    game_date = schedule_data[0].get("game_date")
    if schedule_data[0]["home_id"] == TEAM_ID_REDS:
        opponent = schedule_data[0].get("away_name", "Unknown")
        game_location = "Home"
    else:
        opponent = schedule_data[0].get("home_name", "Unknown")
        game_location = "Away"

    print(f"Game Date: {game_date}, Opponent: {opponent}, Location: {game_location}")

    # Pull Statcast for the day (do NOT assume it exists in offseason)
    statcast_data = safe_pybaseball_call(statcast, start_dt=game_date, end_dt=game_date, team=TEAM_ID_REDS)
    if statcast_data is None or statcast_data.empty:
        print(f"No Statcast data for {game_date} (offseason/spring training likely).")
        return pd.DataFrame(), None, game_date, opponent

    # Confirm schema
    if "player_name" not in statcast_data.columns:
        print("Statcast returned data but missing 'player_name'. Columns:", list(statcast_data.columns))
        return pd.DataFrame(), None, game_date, opponent

    # Reds pitchers list from pybaseball is optional; but here we can just infer starter from the day’s CIN pitchers
    # Attempt to filter by Reds pitchers name format if possible, but don't crash if it fails.
    season_year = current_season_year()

    reds_pitchers_df = safe_pybaseball_call(pitching_stats, season_year, qual=0)
    reds_pitchers = []
    if reds_pitchers_df is not None and not reds_pitchers_df.empty and "Team" in reds_pitchers_df.columns and "Name" in reds_pitchers_df.columns:
        reds_pitchers = reds_pitchers_df[reds_pitchers_df["Team"] == "CIN"]["Name"].dropna().unique().tolist()

    def reverse_name(name):
        parts = name.split()
        return f"{parts[-1]}, {' '.join(parts[:-1])}"

    reds_pitchers_statcast_format = [reverse_name(n) for n in reds_pitchers] if reds_pitchers else []

    # If we couldn't get Reds pitchers, just use all pitchers in statcast_data
    if reds_pitchers_statcast_format:
        reds_pitch_data = statcast_data[statcast_data["player_name"].isin(reds_pitchers_statcast_format)].copy()
    else:
        reds_pitch_data = statcast_data.copy()

    reds_pitch_data = reds_pitch_data[reds_pitch_data["pitch_type"].notnull()]

    if reds_pitch_data.empty:
        return pd.DataFrame(), None, game_date, opponent

    starter = reds_pitch_data["player_name"].value_counts().idxmax()
    starter_data = reds_pitch_data[reds_pitch_data["player_name"] == starter].copy()

    # Player ID logic for Stuff+ training (safe)
    player_id = None
    starter_clean = starter.strip()

    REDS_PITCHER_IDS = {
        "Martinez, Nick": 683004,
        "Greene, Hunter": 682243,
        "Abbott, Andrew": 681218,
    }
    if starter_clean in REDS_PITCHER_IDS:
        player_id = REDS_PITCHER_IDS[starter_clean]
    else:
        try:
            last, first = starter_clean.split(", ")
            lookup = playerid_lookup(last.strip(), first.strip())
            if lookup.empty:
                lookup = playerid_lookup(last.strip(), "")
            if not lookup.empty:
                player_id = int(lookup.iloc[0]["key_mlbam"])
        except Exception as e:
            print(f"Could not lookup pitcher id for {starter_clean}: {e}")

    # Prior season data optional
    prev_year_data = pd.DataFrame()
    if player_id:
        prev_year_data = safe_pybaseball_call(statcast_pitcher, f"{season_year-1}-03-01", f"{season_year-1}-10-01", player_id)
        if prev_year_data is None:
            prev_year_data = pd.DataFrame()

    combined_data = starter_data.copy()
    if not prev_year_data.empty and "pitch_type" in prev_year_data.columns:
        prev_year_data = prev_year_data[prev_year_data["pitch_type"].notnull()]
        combined_data = pd.concat([starter_data, prev_year_data], ignore_index=True)

    # Compute pitch mix metrics
    pitch_mix = starter_data["pitch_type"].value_counts(normalize=True) * 100
    avg_velocity = starter_data.groupby("pitch_type")["release_speed"].mean()
    spin_rates = starter_data.groupby("pitch_type")["release_spin_rate"].mean()

    whiff_rates = starter_data.groupby("pitch_type")["description"].apply(
        lambda x: (x.isin(["swinging_strike", "swinging_strike_blocked"]).sum()) / len(x) * 100
    )
    csw_rates = starter_data.groupby("pitch_type")["description"].apply(
        lambda x: (x.isin(["called_strike", "swinging_strike", "swinging_strike_blocked"]).sum()) / len(x) * 100
    )
    zone_pct = starter_data.groupby("pitch_type")["zone"].apply(lambda x: (x.between(1, 9).sum()) / len(x) * 100)

    vert_break = starter_data.groupby("pitch_type")["pfx_z"].mean() if ("pfx_z" in starter_data.columns and starter_data["pfx_z"].notnull().any()) else pd.Series(dtype="float64")
    horiz_break = starter_data.groupby("pitch_type")["pfx_x"].mean() if ("pfx_x" in starter_data.columns and starter_data["pfx_x"].notnull().any()) else pd.Series(dtype="float64")

    # Stuff+ (safe)
    features = ["release_speed", "release_spin_rate", "pfx_x", "pfx_z"]
    stuff_plus_by_pitch = pd.Series(100, index=starter_data["pitch_type"].unique())

    if all(f in combined_data.columns for f in features) and "description" in combined_data.columns:
        stuff_data = combined_data.dropna(subset=features + ["description"]).copy()

        desc_weights = {
            "swinging_strike": 1.0,
            "swinging_strike_blocked": 1.0,
            "called_strike": 0.7,
            "foul": 0.3,
            "foul_tip": 0.3,
            "ball": 0.0,
            "ball_in_dirt": 0.0,
            "hit_by_pitch": -1.0,
            "blocked_ball": -0.2,
            "wild_pitch": -1.0,
            "in_play": 0.2,
        }
        stuff_data["stuff_score"] = stuff_data["description"].map(desc_weights).fillna(0.2)

        if "launch_speed" in stuff_data.columns:
            weak_contact_mask = (stuff_data["description"] == "in_play") & (stuff_data["launch_speed"] < 85)
            stuff_data.loc[weak_contact_mask, "stuff_score"] = 0.4

        if len(stuff_data) >= 50:
            try:
                X = stuff_data[features]
                y = stuff_data["stuff_score"]
                model = LinearRegression().fit(X, y)

                recent_data = starter_data.dropna(subset=features).copy()
                if not recent_data.empty:
                    recent_data["stuff_score"] = model.predict(recent_data[features])
                    league_avg_score = model.predict(X).mean() or 1
                    recent_data["Stuff+"] = (recent_data["stuff_score"] / league_avg_score) * 100
                    stuff_plus_by_pitch = recent_data.groupby("pitch_type")["Stuff+"].mean()
            except Exception as e:
                print(f"Stuff+ model failed: {e}")

    pitch_types = pitch_mix.index
    df = pd.DataFrame({
        "Pitch Type": pitch_types,
        "Usage %": pitch_mix[pitch_types].values,
        "Avg Velocity": avg_velocity.reindex(pitch_types).values,
        "Avg Spin Rate": spin_rates.reindex(pitch_types).values,
        "Whiff %": whiff_rates.reindex(pitch_types).values,
        "CSW %": csw_rates.reindex(pitch_types).values,
        "Zone %": zone_pct.reindex(pitch_types).values,
        "Vertical Break": vert_break.reindex(pitch_types).fillna(0).values,
        "Horizontal Break": horiz_break.reindex(pitch_types).fillna(0).values,
        "Stuff+": stuff_plus_by_pitch.reindex(pitch_types).fillna(100).values,
    }).sort_values(by="Usage %", ascending=False).reset_index(drop=True)

    df = df.round(2)

    # Save charts
    try:
        plt.figure(figsize=(4, 4))
        plt.pie(df["Usage %"], labels=df["Pitch Type"], autopct="%1.1f%%", startangle=140, textprops={"fontsize": 9})
        plt.title(f"{starter}'s Pitch Mix on {game_date}", fontsize=10)
        plt.tight_layout()
        plt.savefig("static/pitch_mix.png")
        plt.close()

        plt.figure(figsize=(5.5, 4))
        plt.scatter(df["Avg Velocity"], df["Avg Spin Rate"], c="red", s=60)
        for _, row in df.iterrows():
            plt.text(row["Avg Velocity"] + 0.1, row["Avg Spin Rate"], row["Pitch Type"], fontsize=8)
        plt.title(f"{starter}'s Velo vs Spin Rate", fontsize=10)
        plt.xlabel("Avg Velocity (mph)", fontsize=9)
        plt.ylabel("Avg Spin Rate (rpm)", fontsize=9)
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("static/velo_spin.png")
        plt.close()

        df["Vertical Break (in)"] = (df["Vertical Break"] * 12).round(1)
        df["Horizontal Break (in)"] = (df["Horizontal Break"] * 12).round(1)

        plt.figure(figsize=(5.5, 4))
        plt.scatter(-df["Horizontal Break (in)"], df["Vertical Break (in)"], c="blue", s=60)
        for _, row in df.iterrows():
            plt.text(-row["Horizontal Break (in)"] + 0.2, row["Vertical Break (in)"], row["Pitch Type"], fontsize=8)
        plt.title(f"{starter}'s Pitch Movement", fontsize=10)
        plt.xlabel("Horizontal Break (inches)", fontsize=9)
        plt.ylabel("Vertical Break (inches)", fontsize=9)
        plt.axhline(0, color="gray", linestyle="--", linewidth=0.5)
        plt.axvline(0, color="gray", linestyle="--", linewidth=0.5)
        plt.grid(True)
        plt.tight_layout()
        plt.savefig("static/break_chart.png")
        plt.close()
    except Exception as e:
        print(f"Chart generation failed: {e}")

    return df, starter, game_date, opponent


# -------------------------
# Heatmaps
# -------------------------

def plot_pitch_heatmap(df, title, filename, starter):
    ensure_static_dir()

    df = df.dropna(subset=["plate_x", "plate_z", "pitch_type", "player_name"])
    df["player_name"] = df["player_name"].str.title()
    starter = (starter or "").title()

    df = df[df["player_name"] == starter]
    if df.empty or df["plate_x"].nunique() < 2 or df["plate_z"].nunique() < 2:
        print(f"Not enough data to generate heatmap for: {starter} - {title}")
        return

    avg_locations = df.groupby("pitch_type")[["plate_x", "plate_z"]].mean().reset_index()

    plt.figure(figsize=(5, 6))
    try:
        sns.kdeplot(
            data=df, x="plate_x", y="plate_z",
            fill=True, cmap="Reds", bw_adjust=0.5, levels=100, thresh=0.05
        )
    except Exception as e:
        print(f"Error while plotting KDE: {e}")
        return

    # Strike zone (approx)
    plt.axhline(1.5, color="black", linestyle="--", linewidth=1)
    plt.axhline(3.5, color="black", linestyle="--", linewidth=1)
    plt.axvline(-0.83, color="black", linestyle="--", linewidth=1)
    plt.axvline(0.83, color="black", linestyle="--", linewidth=1)

    for _, row in avg_locations.iterrows():
        pitch = row["pitch_type"]
        x = row["plate_x"]
        z = row["plate_z"]
        plt.scatter(x, z, s=100, edgecolors="black", facecolor="white", zorder=5)
        plt.text(x + 0.05, z + 0.05, pitch, fontsize=9, color="black", weight="bold", zorder=6)

    plt.title(title)
    plt.xlabel("Horizontal Location (plate_x)")
    plt.ylabel("Vertical Location (plate_z)")
    plt.tight_layout()

    full_path = os.path.join("static", filename)
    try:
        plt.savefig(full_path)
        print(f"Heatmap saved as {full_path}")
    except Exception as e:
        print(f"Failed to save heatmap: {e}")
    plt.close()


def generate_heatmaps_for_pitches(df, starter):
    if df is None or df.empty:
        print("No data for heatmaps.")
        return
    if "description" not in df.columns:
        print("Error: 'description' column not found in Statcast data.")
        return
    if "player_name" not in df.columns:
        print("Error: 'player_name' column not found in Statcast data.")
        return

    plot_pitch_heatmap(df, f"All Pitches Heatmap {starter}", "heatmap_all.png", starter)

    called_strikes = df[df["description"] == "called_strike"].dropna(subset=["plate_x", "plate_z"])
    plot_pitch_heatmap(called_strikes, f"Called Strikes Heatmap {starter}", "heatmap_called_strikes.png", starter)

    whiffs = df[df["description"].isin(["swinging_strike", "swinging_strike_blocked"])].dropna(subset=["plate_x", "plate_z"])
    plot_pitch_heatmap(whiffs, f"Whiffs Heatmap {starter}", "heatmap_whiffs.png", starter)


# -------------------------
# News scraping
# -------------------------

def scrape_espn():
    url = "https://www.espn.com/mlb/team/_/name/cin/cincinnati-reds"
    soup = get_soup(url)
    news = []

    if soup:
        articles = soup.find_all("a", class_="contentItem__content")
        for link in articles:
            title = link.get_text().strip()
            href = link.get("href", "")

            author = "Unknown Author"
            date = "Unknown Date"
            meta = link.find("ul", class_="contentItem__publicationMeta")
            if meta:
                date_tag = meta.find("li", class_="time-elapsed")
                author_tag = meta.find("li", class_="author")
                if date_tag:
                    date = date_tag.get_text()
                if author_tag:
                    author = author_tag.get_text()

            full_url = href if href.startswith("https") else f"https://www.espn.com{href}"
            news.append({"title": title, "url": full_url, "author": author, "source": "ESPN", "date": date})

    print(f"Found {len(news)} ESPN articles.")
    return news


import feedparser

FOX_MLB_RSS = "https://api.foxsports.com/v2/content/optimized-rss?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=80&tags=fs%2Fmlb"

def scrape_fox():
    feed = feedparser.parse(FOX_MLB_RSS)
    entries = getattr(feed, "entries", []) or []
    print(f"Fox RSS total entries fetched: {len(entries)}")

    # DEBUG: show first 5 titles so we know what Fox is giving you
    for e in entries[:5]:
        print("FOX SAMPLE TITLE:", getattr(e, "title", ""))

    news = []
    for e in entries:
        title = getattr(e, "title", "") or ""
        link = getattr(e, "link", "") or ""
        summary = getattr(e, "summary", "") or ""

        blob = f"{title} {summary}".lower()
        if ("reds" not in blob) and ("cincinnati" not in blob):
            continue

        date = getattr(e, "published", getattr(e, "updated", ""))
        news.append({
            "title": title or "No Title",
            "url": link,
            "author": "Fox Sports",
            "source": "Fox Sports",
            "date": date or "Unknown Date"
        })

    print(f"Found {len(news)} Fox Sports RSS articles (Reds/cincinnati match in title/summary).")
    return news


def scrape_cbs():
    url = "https://www.cbssports.com/mlb/teams/CIN/cincinnati-reds/"
    soup = get_soup(url)
    news = []

    if soup:
        articles = soup.find_all("article", class_="NewsFeed-container")
        for article in articles:
            link = article.find("a", href=True)
            if not link:
                continue

            title_tag = article.find_next("h3")
            title = title_tag.get_text().strip() if title_tag else "No Title"
            href = link["href"]
            full_url = href if href.startswith("https") else f"https://www.cbssports.com{href}"

            author = "Unknown Author"
            date = "Unknown Date"
            parent = article.find_next("div", class_="NewsFeed-byline")
            if parent:
                author_tag = parent.find("span", class_="NewsFeed-author")
                date_tag = parent.find("time")
                if author_tag:
                    author = author_tag.get_text()
                if date_tag:
                    date = date_tag.get_text()

            news.append({"title": title, "url": full_url, "author": author, "source": "CBS Sports", "date": date})

    print(f"Found {len(news)} CBS articles.")
    return news


import feedparser
from datetime import datetime

def scrape_sbnation():
    feed_url = "https://www.redreporter.com/rss/current.xml"
    news = []

    feed = feedparser.parse(feed_url)
    if getattr(feed, "bozo", 0):
        print(f"Red Reporter RSS parse issue: {getattr(feed, 'bozo_exception', '')}")

    entries = getattr(feed, "entries", []) or []
    for e in entries[:20]:
        title = getattr(e, "title", "No Title")
        url = getattr(e, "link", "")
        author = getattr(e, "author", "Red Reporter")
        # dates vary by feed; try a few common fields
        date = ""
        if hasattr(e, "published"):
            date = e.published
        elif hasattr(e, "updated"):
            date = e.updated
        else:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        news.append({
            "title": title,
            "url": url,
            "author": author,
            "source": "Red Reporter",
            "date": date
        })

    print(f"Found {len(news)} Red Reporter RSS entries.")
    return news


def scrape_fangraphs():
    url = "https://blogs.fangraphs.com/"
    soup = get_soup(url)
    news = []

    if soup:
        posts = soup.find_all("div", class_="post")
        for post in posts:
            a = post.find_next("a")
            if not a:
                continue
            title = a.get_text().strip()
            href = a.get("href", "")
            author = "Unknown Author"
            date = "Unknown Date"

            author_tag = post.find("div", class_="postmeta_author")
            if author_tag:
                author = author_tag.get_text(strip=True)
            # date parsing varies; keep as is if present
            divs = post.find_all("div")
            if len(divs) >= 3:
                date = divs[2].get_text(strip=True)

            news.append({"title": title, "url": href, "author": author, "source": "FanGraphs", "date": date})

    print(f"Found {len(news)} FanGraphs articles.")
    return news


def extract_date_from_url(url):
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url or "")
    if match:
        year, month, day = match.groups()
        date = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
        return date.strftime("%Y-%m-%d %H:%M:%S")
    return "Unknown Date"


ATHLETIC_COOKIE_HEADER = os.getenv("ATHLETIC_COOKIES", "")

def parse_cookie_header(cookie_header: str) -> dict:
    cookies = {}
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


import json
import html
import re
from bs4 import BeautifulSoup

def _find_next_data_json(html_text: str):
    """
    Extract Next.js __NEXT_DATA__ JSON if present.
    Returns dict or None.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None


def _walk(obj):
    """Yield all nested dicts/lists/scalars (DFS)"""
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            for v in cur.values():
                stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                stack.append(v)


def _normalize_athletic_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = "https://theathletic.com" + u
    return u


def scrape_athletic():
    """
    Scrape The Athletic Reds page using logged-in cookies.
    Works even when the HTML has no <a href> tags by parsing __NEXT_DATA__.
    """
    url = "https://theathletic.com/mlb/team/reds/"
    cookies = parse_cookie_header(ATHLETIC_COOKIE_HEADER)

    if not cookies:
        print("Athletic cookies not set; skipping Athletic.")
        return []

    resp = requests.get(url, headers=DEFAULT_HEADERS, cookies=cookies, timeout=20)
    print("ATHLETIC PAGE STATUS:", resp.status_code, "len:", len(resp.text))
    if resp.status_code != 200:
        return []

    html_text = resp.text

    # ---------- 1) Try Next.js embedded JSON ----------
    data = _find_next_data_json(html_text)
    news = []

    # Athletic article URLs commonly look like:
    # https://theathletic.com/1234567/...
    article_url_re = re.compile(r"theathletic\.com/\d{6,9}/")

    if data is not None:
        # Heuristic: look for dicts that contain a title-like field and a url-like field
        title_keys = ("title", "headline", "label", "name")
        url_keys = ("url", "href", "permalink", "canonicalUrl", "canonical_url", "shareUrl", "webUrl")

        for node in _walk(data):
            if not isinstance(node, dict):
                continue

            # find a title
            title = None
            for tk in title_keys:
                v = node.get(tk)
                if isinstance(v, str) and len(v.strip()) >= 12:
                    title = html.unescape(v.strip())
                    break

            if not title:
                continue

            # find a url/href
            found_url = None
            for uk in url_keys:
                v = node.get(uk)
                if isinstance(v, str) and v:
                    candidate = _normalize_athletic_url(v)
                    if article_url_re.search(candidate):
                        found_url = candidate
                        break

            if not found_url:
                # sometimes link lives under nested structures; quick check common shapes
                link = node.get("link") or node.get("links")
                if isinstance(link, dict):
                    for uk in url_keys:
                        v = link.get(uk)
                        if isinstance(v, str) and v:
                            candidate = _normalize_athletic_url(v)
                            if article_url_re.search(candidate):
                                found_url = candidate
                                break

            if found_url:
                news.append({
                    "title": title,
                    "url": found_url,
                    "author": "The Athletic",
                    "source": "The Athletic",
                    "date": "Unknown Date"
                })

        # de-dupe by URL
        deduped = {item["url"]: item for item in news}
        news = list(deduped.values())

    # ---------- 2) Fallback: regex URLs out of HTML ----------
    if not news:
        # Pull full URLs
        urls = set(re.findall(r'https://theathletic\.com/\d{6,9}/[^"\s<>]+', html_text))
        # Pull relative URLs
        rels = set(re.findall(r'"/\d{6,9}/[^"\s<>]+"\s', html_text))
        rels = {r.strip().strip('"').strip() for r in rels}
        urls |= {("https://theathletic.com" + r) for r in rels}

        urls = [u for u in urls if article_url_re.search(u)]
        urls = sorted(urls)[:25]

        news = [{
            "title": "(Athletic article)",
            "url": u,
            "author": "The Athletic",
            "source": "The Athletic",
            "date": "Unknown Date"
        } for u in urls]

    # optional: tighten — remove obvious non-article pages if any sneak in
    news = [n for n in news if article_url_re.search(n["url"])]

    print(f"Found {len(news)} Athletic articles via cookies/NextData.")
    return news[:20]

# -------------------------
# Bluesky
# -------------------------

USERNAME = os.getenv("BLUESKY_USERNAME")
PASSWORD = os.getenv("BLUESKY_PASSWORD")

def bluesky_login():
    if not USERNAME or not PASSWORD:
        print("Bluesky creds not set; skipping Bluesky.")
        return None

    url = "https://bsky.social/xrpc/com.atproto.server.createSession"
    data = {"identifier": USERNAME, "password": PASSWORD}

    try:
        response = requests.post(url, json=data, timeout=20)
        response.raise_for_status()
        token = response.json().get("accessJwt")
        print("Successfully authenticated with Bluesky.")
        return token
    except Exception as e:
        print(f"Failed to authenticate with Bluesky: {e}")
        return None


def skeet_uri_to_url(handle: str, uri: str) -> str:
    """
    Convert an at:// URI to a public bsky.app URL.
    Example:
      handle = "ctrent.bsky.social"
      uri    = "at://did:plc.../app.bsky.feed.post/3lxyzabcd..."
      -> https://bsky.app/profile/ctrent.bsky.social/post/3lxyzabcd...
    """
    if not handle or not uri:
        return ""
    try:
        rkey = uri.rstrip("/").split("/")[-1]
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    except Exception:
        return ""


def humanize_time_ago(dt: datetime, now=None) -> str:
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    if h < 48:
        return f"{h}h ago"
    d = h // 24
    return f"{d}d ago"


def linkify_bsky_text(text: str) -> str:
    """
    Minimal linkifier:
    - already extracts URLs separately; this mainly turns @handles + #tags into links
    - keeps it simple (not full facet parsing)
    """
    if not text:
        return ""

    # Escape first? You are currently using |safe in template, so be careful.
    # If you keep |safe, you should HTML-escape here before injecting links.
    import html as _html
    safe = _html.escape(text)

    # URLs (optional: if you want inline linkification too)
    safe = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', safe)

    # @handle (domain-style handles)
    safe = re.sub(
        r"@([a-zA-Z0-9][a-zA-Z0-9\.\-]+)",
        r'<a href="https://bsky.app/profile/\1" target="_blank" rel="noopener noreferrer">@\1</a>',
        safe
    )

    # #hashtag
    safe = re.sub(
        r"#([A-Za-z0-9_]+)",
        r'<a href="https://bsky.app/hashtag/\1" target="_blank" rel="noopener noreferrer">#\1</a>',
        safe
    )

    return safe


def extract_bsky_media(post: dict) -> list[dict]:
    """
    Returns a list of media items, e.g.
      {"type":"image","thumb": "...","full": "...","alt":"..."}
      {"type":"external","uri":"...","title":"...","description":"...","thumb":"..."}
    """
    out = []
    if not post:
        return out

    embed = post.get("embed") or {}
    etype = embed.get("$type", "")

    # Images
    if "app.bsky.embed.images" in etype:
        for img in embed.get("images", []) or []:
            out.append({
                "type": "image",
                "thumb": img.get("thumb", ""),
                "full": img.get("fullsize", ""),
                "alt": img.get("alt", ""),
            })

    # External card (link preview)
    if "app.bsky.embed.external" in etype:
        ext = (embed.get("external") or {})
        out.append({
            "type": "external",
            "uri": ext.get("uri", ""),
            "title": ext.get("title", ""),
            "description": ext.get("description", ""),
            "thumb": ext.get("thumb", ""),
        })

    return out

def fetch_reds_skeets(token):
    if not token:
        return []

    skeets = []
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    usernames = [
        "ctrent.bsky.social",
        "msheldon.bsky.social",
        "nicholaspkirby.bsky.social",
        "dougdirt24.bsky.social",
        "gdubmlb.bsky.social",
        "redreporter.bsky.social",
        "mollyknight.bsky.social",
        "joeposnanski.bsky.social",
        "jmorris14.bsky.social",
        "enosarris.bsky.social",
        "keithlaw.bsky.social",
        "jaysonst.bsky.social",
        "ken-rosenthal.bsky.social",
        "peteabeglobe.bsky.social",
        "feinsand.bsky.social",
        "dgoold.bsky.social",
    ]

    def fetch_limited_replies(skeet_uri, max_replies=3):
        replies = []
        try:
            response = requests.get(
                f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?post={skeet_uri}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if response.status_code != 200:
                return replies

            data = response.json()
            thread = data.get("thread", {}).get("replies", [])[:max_replies]
            for reply in thread:
                post = reply.get("post", {})
                reply_text = post.get("record", {}).get("text", "No Text")
                reply_uri = post.get("uri", "")
                reply_handle = post.get("author", {}).get("handle", "Unknown")
                reply_timestamp = parse_timestamp(post.get("record", {}).get("createdAt", ""))
                if not reply_timestamp:
                    continue
                if reply_timestamp.tzinfo is None:
                    reply_timestamp = reply_timestamp.replace(tzinfo=timezone.utc)
                if reply_timestamp < one_week_ago:
                    continue

                replies.append({
                    "author": post.get("author", {}).get("handle", "Unknown"),
                    "display_name": post.get("author", {}).get("displayName", "Unknown"),
                    "avatar_url": post.get("author", {}).get("avatar", ""),
                    "text": reply_text,
                    "timestamp": reply_timestamp.isoformat(),
                    "media": [],
                    "links": extract_links(reply_text),
                    "replies": [],
                    "uri": reply_uri,
                    "url": skeet_uri_to_url(author_handle, skeet_uri),
                    "time_ago": humanize_time_ago(timestamp),
                    "html_text": linkify_bsky_text(text),
                    "media": media,
                })
        except Exception as e:
            print(f"Error fetching replies: {e}")
        return replies

    try:
        for username in usernames:
            response = requests.get(
                f"https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit=5",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            if response.status_code != 200:
                continue

            data = response.json()
            for item in data.get("feed", [])[:5]:
                post = item.get("post", {})
                text = post.get("record", {}).get("text", "No Text")
                timestamp = parse_timestamp(post.get("record", {}).get("createdAt", ""))
                if not timestamp:
                    continue
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp < one_week_ago:
                    continue

                skeet_uri = post.get("uri", "")  # use uri, not id
                replies = fetch_limited_replies(skeet_uri)
                author_handle = post.get("author", {}).get("handle", "Unknown")
                media = extract_bsky_media(post)

                skeets.append({
                    "author": post.get("author", {}).get("handle", "Unknown"),
                    "display_name": post.get("author", {}).get("displayName", "Unknown"),
                    "avatar_url": post.get("author", {}).get("avatar", ""),
                    "text": text,
                    "timestamp": timestamp.isoformat(),
                    "media": [],
                    "links": extract_links(text),
                    "replies": replies,

                    # NEW:
                    "uri": skeet_uri,
                    "url": skeet_uri_to_url(author_handle, skeet_uri),
                    "time_ago": humanize_time_ago(timestamp),
                    "html_text": linkify_bsky_text(text),
                    "media": media,
                })

        skeets.sort(key=lambda x: parse_timestamp(x["timestamp"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        print(f"Total Reds-related skeets found: {len(skeets)}")
    except Exception as e:
        print(f"Failed to fetch Reds skeets: {e}")

    return skeets


# -------------------------
# Reddit
# -------------------------

def fetch_reddit_posts(subreddit, limit=15):
    try:
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        user_agent = os.getenv("REDDIT_USER_AGENT")

        if not client_id or not client_secret or not user_agent:
            print("Reddit creds not set; skipping reddit.")
            return []

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        posts = []
        subreddit_obj = reddit.subreddit(subreddit)
        for submission in subreddit_obj.new(limit=limit):
            posts.append({
                "title": submission.title,
                "author": submission.author.name if submission.author else "Unknown",
                "score": submission.score,
                "comments": submission.num_comments,
                "link": f"https://www.reddit.com{submission.permalink}",
                "timestamp": datetime.utcfromtimestamp(submission.created_utc).strftime("%Y-%m-%d %H:%M:%S"),
                "subreddit": subreddit,
            })

        posts.sort(key=lambda x: datetime.strptime(x["timestamp"], "%Y-%m-%d %H:%M:%S"), reverse=True)
        return posts
    except Exception as e:
        print(f"Error fetching posts from r/{subreddit}: {e}")
        return []


# -------------------------
# Leaderboards + hot/cold
# -------------------------

def get_reds_leaderboard():
    season_year = current_season_year()
    df = safe_pybaseball_call(batting_stats, season_year, qual=1)
    if df is None or df.empty:
        return {}

    if "Team" not in df.columns or "Name" not in df.columns:
        return {}

    # You can adjust stat list if columns change
    stat_keys = [
        "AVG", "OBP", "SLG", "OPS", "HR", "RBI",
        "wRC+", "WAR", "xBA", "xSLG", "xwOBA", "HardHit%", "maxEV", "L-WAR"
    ]

    reds_df = df.dropna(subset=["Team"])
    reds_df = reds_df[reds_df["Team"] == "CIN"].reset_index(drop=True)
    if reds_df.empty:
        return {}

    leaderboards = {}
    for stat in stat_keys:
        if stat not in df.columns:
            continue
        league_values = df[stat].dropna()
        top_reds = reds_df.sort_values(by=stat, ascending=False).head(5)

        rows = []
        for _, player in top_reds.iterrows():
            name = player["Name"]
            value = player[stat]
            if pd.isna(value):
                continue

            if isinstance(value, float):
                if stat in ["AVG", "OBP", "SLG", "OPS", "xBA", "xSLG", "xwOBA"]:
                    value = round(value, 3)
                elif stat in ["HardHit%", "maxEV"]:
                    value = round(value, 1)
                else:
                    value = round(value)

            percentile = round(percentileofscore(league_values, player[stat], kind="rank"), 1)
            rows.append({"Name": name, "Value": value, "Percentile": f"{percentile}%"})

        leaderboards[stat] = rows

    return leaderboards


def get_reds_pitching_leaderboard():
    season_year = current_season_year()
    df = safe_pybaseball_call(pitching_stats, season_year, qual=0)
    if df is None or df.empty:
        return {}

    if "Team" not in df.columns or "Name" not in df.columns:
        return {}

    reds_df = df[df["Team"] == "CIN"]
    if reds_df.empty:
        return {}

    stat_keys = ["ERA", "FIP", "xFIP", "K/9", "BB/9", "WAR"]
    leaderboard = {}

    for stat in stat_keys:
        if stat not in df.columns:
            continue

        lower_is_better = stat in ["ERA", "FIP", "xFIP", "BB/9"]
        league_sorted = df.dropna(subset=[stat]).sort_values(by=stat, ascending=lower_is_better).reset_index(drop=True)
        total = len(league_sorted)

        top_reds = []
        for _, row in reds_df.iterrows():
            if pd.isna(row[stat]):
                continue
            name = row["Name"]
            value = round(row[stat], 3) if isinstance(row[stat], (float, int)) else row[stat]
            rank_idx = league_sorted[league_sorted["Name"] == name].index
            if len(rank_idx) == 0:
                continue
            rank = int(rank_idx[0]) + 1
            percentile = round((1 - (rank - 1) / total) * 100, 1) if total else None
            top_reds.append({"Name": name, "Value": value, "Percentile": f"{percentile}%" if percentile is not None else "N/A", "Rank": rank})

        top_reds = sorted(top_reds, key=lambda x: x["Rank"])[:5]
        leaderboard[stat] = top_reds

    return leaderboard


def get_recent_dates():
    end = datetime.today()
    start = end - timedelta(days=7)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def get_hot_cold_batters():
    # Keep your existing logic if you want; this is just guarded against offseason statcast.
    start, end = get_recent_dates()
    df = safe_pybaseball_call(statcast, start, end)
    if df is None or df.empty:
        return [], []

    # You can keep your full existing implementation here if you prefer.
    return [], []


def get_hot_cold_pitchers():
    start, end = get_recent_dates()
    df = safe_pybaseball_call(statcast, start, end)
    if df is None or df.empty:
        return [], []
    return [], []


# -------------------------
# Aggregate all content
# -------------------------

def aggregate_news():
    news_sources = [
        scrape_espn,
        scrape_fox,
        scrape_cbs,
        scrape_sbnation,   # your RSS Red Reporter version
        scrape_fangraphs,
        scrape_athletic,   # keep it, but it should safely return []
    ]

    news = []

    for source in news_sources:
        # Skip None or non-callables safely
        if not callable(source):
            print(f"News source skipped (not callable): {source}")
            continue

        try:
            items = source() or []
            news.extend(items)
        except Exception as e:
            # Don't assume __name__ exists
            name = getattr(source, "__name__", str(source))
            print(f"News source failed ({name}): {e}")

    sorted_news = sort_articles(news)
    print(f"Total aggregated articles: {len(sorted_news)}")

    # Bluesky + everything else unchanged
    token = bluesky_login()
    skeets = fetch_reds_skeets(token)

    reds_batting_leaderboards = get_reds_leaderboard()
    reds_pitching_leaderboards = get_reds_pitching_leaderboard()

    hot_batters, cold_batters = get_hot_cold_batters()
    hot_pitchers, cold_pitchers = get_hot_cold_pitchers()

    schedule = fetch_reds_schedule()

    latest_game_summary, latest_game_details, boxscore, game_link = get_latest_game(113)
    upcoming_game_summary, upcoming_game_pitchers = get_upcoming_game(113)

    reddit_reds = fetch_reddit_posts("Reds", limit=15)
    reddit_baseball = fetch_reddit_posts("baseball", limit=15)
    reddit_posts = reddit_reds + reddit_baseball
    reddit_posts = sorted(
        reddit_posts,
        key=lambda x: datetime.strptime(x['timestamp'], '%Y-%m-%d %H:%M:%S'),
        reverse=True
    )

    return (
        sorted_news,
        skeets,
        latest_game_summary,
        latest_game_details,
        boxscore,
        game_link,
        upcoming_game_summary,
        upcoming_game_pitchers,
        reds_batting_leaderboards,
        reds_pitching_leaderboards,
        schedule,
        reddit_posts,
        hot_batters,
        cold_batters,
        hot_pitchers,
        cold_pitchers,
    )