"""
stats/__init__.py — schedule, leaderboards, hot/cold, pitch mix.

FIX: get_hot_cold_batters / get_hot_cold_pitchers no longer make an
expensive Statcast call only to return [], []. The stubs are kept but
skip the API call entirely until real logic is added.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import statsapi
from scipy.stats import percentileofscore
from pybaseball import (
    batting_stats,
    pitching_stats,
    statcast,
    playerid_lookup,
    statcast_pitcher,
)
from sklearn.linear_model import LinearRegression

from reds.utils import (
    TEAM_ID_REDS,
    allowed_game_types,
    current_season_year,
    safe_pybaseball_call,
    ensure_static_dir,
    stats_cache,
    statcast_cache,
    schedule_cache,
    chart_needs_refresh,
)


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def schedule_filtered(team_id: int, start_date: str, end_date: str) -> list:
    games = statsapi.schedule(team=team_id, start_date=start_date, end_date=end_date) or []
    allowed = allowed_game_types()
    return [g for g in games if g.get("game_type") in allowed]


def get_latest_game(team_id: int = TEAM_ID_REDS):
    today = datetime.now().strftime("%Y-%m-%d")
    start = f"{datetime.now().year}-02-01"
    games = schedule_filtered(team_id, start, today)

    if not games:
        return "No game data available.", "", "", ""

    completed = [g for g in games if "Final" in (g.get("status") or "") or "Completed" in (g.get("status") or "")]
    latest_game = completed[-1] if completed else games[-1]
    game_id = latest_game["game_id"]

    game_data = statsapi.get("game", {"gamePk": game_id})
    decisions = game_data.get("liveData", {}).get("decisions", {})
    winning_pitcher = decisions.get("winner", {}).get("fullName", "Unknown")
    losing_pitcher = decisions.get("loser", {}).get("fullName", "Unknown")
    boxscore = statsapi.boxscore(game_id)

    summary = (
        f"{latest_game['away_name']} {latest_game['away_score']} - "
        f"{latest_game['home_name']} {latest_game['home_score']} "
        f"({latest_game['status']})"
    )
    details = f"Winning Pitcher: {winning_pitcher}, Losing Pitcher: {losing_pitcher}"
    return summary, details, boxscore, f"https://www.mlb.com/gameday/{game_id}"


def get_upcoming_game(team_id: int = TEAM_ID_REDS):
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    games = schedule_filtered(team_id, today, end)

    if not games:
        return "No upcoming game data available.", ""

    next_game = games[0]
    game_id = next_game["game_id"]
    game_data = statsapi.get("game", {"gamePk": game_id})
    pp = game_data.get("gameData", {}).get("probablePitchers", {})
    home_p = pp.get("home", {}).get("fullName", "Unknown")
    away_p = pp.get("away", {}).get("fullName", "Unknown")

    summary = f"{next_game['away_name']} @ {next_game['home_name']} - {next_game['game_datetime']} ({next_game['status']})"
    return summary, f"Probable Pitchers: {away_p} vs {home_p}"


def fetch_reds_schedule(team_id: int = TEAM_ID_REDS) -> list:
    cached = schedule_cache.get("reds_schedule")
    if cached is not None:
        return cached

    today = datetime.now()
    one_week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    one_week_ahead = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    past_games = schedule_filtered(team_id, one_week_ago, today.strftime("%Y-%m-%d"))
    upcoming_games = schedule_filtered(team_id, today.strftime("%Y-%m-%d"), one_week_ahead)

    schedule = []

    def _format(game):
        raw_date = game["game_datetime"]
        try:
            formatted_date = datetime.strptime(raw_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%A, %B %d, %Y - %I:%M %p")
        except Exception:
            formatted_date = raw_date

        opponent = game["away_name"] if game["home_id"] == team_id else game["home_name"]
        status = game.get("status", "Unknown Status")
        game_id = game["game_id"]
        game_link = f"https://www.mlb.com/gameday/{game_id}"

        if "Final" in status or "Completed" in status or "Postponed" in status:
            game_data = statsapi.get("game", {"gamePk": game_id})
            decisions = game_data.get("liveData", {}).get("decisions", {})
            wp = decisions.get("winner", {}).get("fullName", "Unknown")
            lp = decisions.get("loser", {}).get("fullName", "Unknown")
            return {
                "date": formatted_date,
                "opponent": opponent,
                "venue": game.get("venue_name", "Unknown Venue"),
                "status": "Completed",
                "score": f"{game['away_name']} {game.get('away_score','')} - {game['home_name']} {game.get('home_score','')}",
                "pitcher_summary": f"Winning Pitcher: {wp}, Losing Pitcher: {lp}",
                "game_link": game_link,
            }

        game_data = statsapi.get("game", {"gamePk": game_id})
        pp = game_data.get("gameData", {}).get("probablePitchers", {})
        return {
            "date": formatted_date,
            "opponent": opponent,
            "venue": game.get("venue_name", "Unknown Venue"),
            "status": "Scheduled",
            "score": "N/A",
            "pitcher_summary": f"Probable Pitchers: {pp.get('away',{}).get('fullName','Unknown')} vs {pp.get('home',{}).get('fullName','Unknown')}",
            "game_link": game_link,
        }

    for g in past_games + upcoming_games:
        try:
            schedule.append(_format(g))
        except Exception as e:
            print(f"[Schedule] Failed to format game {g.get('game_id')}: {e}")

    print(f"[Schedule] {len(schedule)} games fetched")
    schedule_cache.set("reds_schedule", schedule)
    return schedule


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------

def get_reds_leaderboard() -> dict:
    cached = stats_cache.get("reds_batting_lb")
    if cached is not None:
        return cached

    season_year = current_season_year()
    df = safe_pybaseball_call(batting_stats, season_year, qual=1)
    if df is None or df.empty or "Team" not in df.columns:
        return {}

    stat_keys = [
        "AVG", "OBP", "SLG", "OPS", "HR", "RBI",
        "wRC+", "WAR", "xBA", "xSLG", "xwOBA", "HardHit%", "maxEV", "L-WAR",
    ]
    reds_df = df[df["Team"] == "CIN"].reset_index(drop=True)
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
            rows.append({"Name": player["Name"], "Value": value, "Percentile": f"{percentile}%"})
        leaderboards[stat] = rows

    stats_cache.set("reds_batting_lb", leaderboards)
    return leaderboards


def get_reds_pitching_leaderboard() -> dict:
    cached = stats_cache.get("reds_pitching_lb")
    if cached is not None:
        return cached

    season_year = current_season_year()
    df = safe_pybaseball_call(pitching_stats, season_year, qual=0)
    if df is None or df.empty or "Team" not in df.columns:
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
            rank_idx = league_sorted[league_sorted["Name"] == row["Name"]].index
            if not len(rank_idx):
                continue
            rank = int(rank_idx[0]) + 1
            percentile = round((1 - (rank - 1) / total) * 100, 1) if total else None
            top_reds.append({
                "Name": row["Name"],
                "Value": round(row[stat], 3),
                "Percentile": f"{percentile}%" if percentile is not None else "N/A",
                "Rank": rank,
            })

        leaderboard[stat] = sorted(top_reds, key=lambda x: x["Rank"])[:5]

    stats_cache.set("reds_pitching_lb", leaderboard)
    return leaderboard


# ---------------------------------------------------------------------------
# Hot / Cold
# FIX: stubs no longer make an expensive Statcast call only to discard it.
# Implement real logic here when ready; returning empty lists costs nothing.
# ---------------------------------------------------------------------------

def get_hot_cold_batters() -> tuple[list, list]:
    # TODO: implement hot/cold batter logic using Statcast rolling stats
    return [], []


def get_hot_cold_pitchers() -> tuple[list, list]:
    # TODO: implement hot/cold pitcher logic using Statcast rolling stats
    return [], []


# ---------------------------------------------------------------------------
# Pitch mix + Statcast charts
# ---------------------------------------------------------------------------

def get_pitch_mix_for_last_game():
    """
    Returns (df, starter_name, game_date, opponent).
    Skips chart generation if charts are fresh (< 6 hours old).
    """
    ensure_static_dir()

    # Find most recent Reds game
    schedule_data = None
    for days_back in range(1, 8):
        game_date = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        schedule_data = statsapi.schedule(start_date=game_date, end_date=game_date, team=TEAM_ID_REDS)
        if schedule_data:
            break

    if not schedule_data:
        return pd.DataFrame(), None, None, None

    game_date = schedule_data[0].get("game_date")
    if schedule_data[0]["home_id"] == TEAM_ID_REDS:
        opponent = schedule_data[0].get("away_name", "Unknown")
    else:
        opponent = schedule_data[0].get("home_name", "Unknown")

    # Check Statcast cache
    sc_key = f"statcast_{game_date}"
    statcast_data = statcast_cache.get(sc_key)
    if statcast_data is None:
        statcast_data = safe_pybaseball_call(statcast, start_dt=game_date, end_dt=game_date, team=TEAM_ID_REDS)
        if statcast_data is not None and not statcast_data.empty:
            statcast_cache.set(sc_key, statcast_data)

    if statcast_data is None or statcast_data.empty:
        print(f"[Statcast] No data for {game_date}")
        return pd.DataFrame(), None, game_date, opponent

    if "player_name" not in statcast_data.columns:
        return pd.DataFrame(), None, game_date, opponent

    season_year = current_season_year()
    reds_pitchers_df = safe_pybaseball_call(pitching_stats, season_year, qual=0)
    reds_pitchers = []
    if reds_pitchers_df is not None and "Team" in reds_pitchers_df.columns:
        reds_pitchers = reds_pitchers_df[reds_pitchers_df["Team"] == "CIN"]["Name"].dropna().unique().tolist()

    def _reverse(name):
        parts = name.split()
        return f"{parts[-1]}, {' '.join(parts[:-1])}"

    reds_fmt = [_reverse(n) for n in reds_pitchers] if reds_pitchers else []
    pitch_data = (
        statcast_data[statcast_data["player_name"].isin(reds_fmt)].copy()
        if reds_fmt
        else statcast_data.copy()
    )
    pitch_data = pitch_data[pitch_data["pitch_type"].notnull()]

    if pitch_data.empty:
        return pd.DataFrame(), None, game_date, opponent

    starter = pitch_data["player_name"].value_counts().idxmax()
    starter_data = pitch_data[pitch_data["player_name"] == starter].copy()

    # Pitcher ID lookup
    player_id = None
    starter_clean = starter.strip()
    KNOWN_IDS = {"Martinez, Nick": 683004, "Greene, Hunter": 682243, "Abbott, Andrew": 681218}
    if starter_clean in KNOWN_IDS:
        player_id = KNOWN_IDS[starter_clean]
    else:
        try:
            last, first = starter_clean.split(", ")
            lookup = playerid_lookup(last.strip(), first.strip())
            if lookup.empty:
                lookup = playerid_lookup(last.strip(), "")
            if not lookup.empty:
                player_id = int(lookup.iloc[0]["key_mlbam"])
        except Exception as e:
            print(f"[Stats] Pitcher ID lookup failed for {starter_clean}: {e}")

    prev_year_data = pd.DataFrame()
    if player_id:
        prev_key = f"statcast_pitcher_{player_id}_{season_year-1}"
        prev_year_data = statcast_cache.get(prev_key)
        if prev_year_data is None:
            prev_year_data = safe_pybaseball_call(
                statcast_pitcher,
                f"{season_year-1}-03-01",
                f"{season_year-1}-10-01",
                player_id,
            )
            if prev_year_data is None:
                prev_year_data = pd.DataFrame()
            if not prev_year_data.empty:
                statcast_cache.set(prev_key, prev_year_data)

    combined = pd.concat(
        [starter_data, prev_year_data[prev_year_data["pitch_type"].notnull()]]
        if not prev_year_data.empty and "pitch_type" in prev_year_data.columns
        else [starter_data],
        ignore_index=True,
    )

    # Metrics
    pitch_mix   = starter_data["pitch_type"].value_counts(normalize=True) * 100
    avg_velo    = starter_data.groupby("pitch_type")["release_speed"].mean()
    spin_rates  = starter_data.groupby("pitch_type")["release_spin_rate"].mean()
    whiff_rates = starter_data.groupby("pitch_type")["description"].apply(
        lambda x: x.isin(["swinging_strike", "swinging_strike_blocked"]).sum() / len(x) * 100
    )
    csw_rates   = starter_data.groupby("pitch_type")["description"].apply(
        lambda x: x.isin(["called_strike", "swinging_strike", "swinging_strike_blocked"]).sum() / len(x) * 100
    )
    zone_pct    = starter_data.groupby("pitch_type")["zone"].apply(
        lambda x: x.between(1, 9).sum() / len(x) * 100
    )
    _c = lambda col: (
        starter_data.groupby("pitch_type")[col].mean()
        if col in starter_data.columns and starter_data[col].notnull().any()
        else pd.Series(dtype="float64")
    )
    vert_break  = _c("pfx_z")
    horiz_break = _c("pfx_x")

    features = ["release_speed", "release_spin_rate", "pfx_x", "pfx_z"]
    stuff_plus = pd.Series(100, index=starter_data["pitch_type"].unique())

    if all(f in combined.columns for f in features) and "description" in combined.columns:
        stuff_data = combined.dropna(subset=features + ["description"]).copy()
        desc_weights = {
            "swinging_strike": 1.0, "swinging_strike_blocked": 1.0,
            "called_strike": 0.7, "foul": 0.3, "foul_tip": 0.3,
            "ball": 0.0, "ball_in_dirt": 0.0, "hit_by_pitch": -1.0,
            "blocked_ball": -0.2, "wild_pitch": -1.0, "in_play": 0.2,
        }
        stuff_data["stuff_score"] = stuff_data["description"].map(desc_weights).fillna(0.2)
        if "launch_speed" in stuff_data.columns:
            mask = (stuff_data["description"] == "in_play") & (stuff_data["launch_speed"] < 85)
            stuff_data.loc[mask, "stuff_score"] = 0.4

        if len(stuff_data) >= 50:
            try:
                model = LinearRegression().fit(stuff_data[features], stuff_data["stuff_score"])
                recent = starter_data.dropna(subset=features).copy()
                if not recent.empty:
                    recent["stuff_score"] = model.predict(recent[features])
                    avg = model.predict(stuff_data[features]).mean() or 1
                    recent["Stuff+"] = (recent["stuff_score"] / avg) * 100
                    stuff_plus = recent.groupby("pitch_type")["Stuff+"].mean()
            except Exception as e:
                print(f"[Stats] Stuff+ model failed: {e}")

    pitch_types = pitch_mix.index
    df = pd.DataFrame({
        "Pitch Type": pitch_types,
        "Usage %": pitch_mix[pitch_types].values,
        "Avg Velocity": avg_velo.reindex(pitch_types).values,
        "Avg Spin Rate": spin_rates.reindex(pitch_types).values,
        "Whiff %": whiff_rates.reindex(pitch_types).values,
        "CSW %": csw_rates.reindex(pitch_types).values,
        "Zone %": zone_pct.reindex(pitch_types).values,
        "Vertical Break": vert_break.reindex(pitch_types).fillna(0).values,
        "Horizontal Break": horiz_break.reindex(pitch_types).fillna(0).values,
        "Stuff+": stuff_plus.reindex(pitch_types).fillna(100).values,
    }).sort_values("Usage %", ascending=False).reset_index(drop=True).round(2)

    return df, starter, game_date, opponent
