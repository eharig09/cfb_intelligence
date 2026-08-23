"""
reds.py — Flask Blueprint, route, and aggregate_news with concurrency.

All heavy lifting is delegated to sub-modules:
  reds/scrapers/  — news scraping
  reds/stats/     — schedule, leaderboards, pitch mix
  reds/social/    — Bluesky, Reddit
  reds/charts/    — Matplotlib chart generation
  reds/utils.py   — shared helpers, TTL cache, config
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from flask import Blueprint, render_template

from reds.utils import (
    TEAM_ID_REDS,
    ensure_static_dir,
    safe_pybaseball_call,
    sort_articles,
    statcast_cache,
)
from reds.scrapers import ALL_SCRAPERS
from reds.stats import (
    schedule_filtered,
    get_latest_game,
    get_upcoming_game,
    fetch_reds_schedule,
    get_reds_leaderboard,
    get_reds_pitching_leaderboard,
    get_hot_cold_batters,
    get_hot_cold_pitchers,
    get_pitch_mix_for_last_game,
)
from reds.social import bluesky_login, fetch_reds_posts, fetch_reddit_posts
from reds.charts import generate_pitch_charts, generate_heatmaps
from pybaseball import statcast as pyb_statcast

reds = Blueprint("reds", __name__, template_folder="../templates")


# ---------------------------------------------------------------------------
# Aggregate with concurrency
# ---------------------------------------------------------------------------

def aggregate_news():
    """
    Run all scrapers and data fetchers in parallel using ThreadPoolExecutor.
    I/O-bound tasks (HTTP, API calls) are a perfect fit for threads.
    Failures in one task never crash the others.
    """
    results: dict = {}

    # --- Define all tasks ---
    def run_scrapers():
        news = []
        # Scrapers themselves are further parallelised
        with ThreadPoolExecutor(max_workers=len(ALL_SCRAPERS)) as ex:
            futures = {ex.submit(fn): fn.__name__ for fn in ALL_SCRAPERS}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    news.extend(future.result() or [])
                except Exception as e:
                    print(f"[Scraper] {name} failed: {e}")
        return sort_articles(news)

    def run_bluesky():
        token = bluesky_login()
        return fetch_reds_posts(token)

    def run_reddit():
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_reds     = ex.submit(fetch_reddit_posts, "Reds",     15)
            f_baseball = ex.submit(fetch_reddit_posts, "baseball", 15)
        posts = f_reds.result() + f_baseball.result()
        return sorted(
            posts,
            key=lambda x: datetime.strptime(x["timestamp"], "%Y-%m-%d %H:%M:%S"),
            reverse=True,
        )

    # All top-level tasks (scrapers count as one task here)
    top_level_tasks = {
        "news":               run_scrapers,
        "bluesky":            run_bluesky,
        "reddit":             run_reddit,
        "schedule":           fetch_reds_schedule,
        "latest_game":        lambda: get_latest_game(TEAM_ID_REDS),
        "upcoming_game":      lambda: get_upcoming_game(TEAM_ID_REDS),
        "batting_lb":         get_reds_leaderboard,
        "pitching_lb":        get_reds_pitching_leaderboard,
        "hot_cold_batters":   get_hot_cold_batters,
        "hot_cold_pitchers":  get_hot_cold_pitchers,
    }

    with ThreadPoolExecutor(max_workers=len(top_level_tasks)) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in top_level_tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"[aggregate_news] Task '{key}' failed: {e}")
                results[key] = None

    # Unpack with safe defaults
    news                    = results.get("news") or []
    skeets                  = results.get("bluesky") or []
    reddit_posts            = results.get("reddit") or []
    schedule                = results.get("schedule") or []
    batting_lb              = results.get("batting_lb") or {}
    pitching_lb             = results.get("pitching_lb") or {}
    hot_batters, cold_batters   = (results.get("hot_cold_batters")  or ([], []))
    hot_pitchers, cold_pitchers = (results.get("hot_cold_pitchers") or ([], []))

    latest_game_result   = results.get("latest_game")   or ("No game data available.", "", "", "")
    upcoming_game_result = results.get("upcoming_game") or ("No upcoming game data available.", "")

    latest_game_summary, latest_game_details, boxscore, game_link = (
        latest_game_result if len(latest_game_result) == 4 else ("No game data available.", "", "", "")
    )
    upcoming_game_summary, upcoming_game_pitchers = (
        upcoming_game_result if len(upcoming_game_result) == 2 else ("No upcoming game data available.", "")
    )

    return (
        news,
        skeets,
        latest_game_summary,
        latest_game_details,
        boxscore,
        game_link,
        upcoming_game_summary,
        upcoming_game_pitchers,
        batting_lb,
        pitching_lb,
        schedule,
        reddit_posts,
        hot_batters,
        cold_batters,
        hot_pitchers,
        cold_pitchers,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@reds.route("/", methods=["GET"])
def home():
    ensure_static_dir()

    # Find most recent game for Statcast context
    schedule_data = None
    for days_back in range(0, 8):
        game_date_try = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        schedule_data = schedule_filtered(TEAM_ID_REDS, game_date_try, game_date_try)
        if schedule_data:
            break

    if not schedule_data:
        game_date = opponent = starter = None
        pitch_summary = []
        statcast_available = False
    else:
        df, starter, game_date, opponent = get_pitch_mix_for_last_game()
        pitch_summary = df.to_dict(orient="records") if (df is not None and not df.empty) else []
        statcast_available = bool(starter) and bool(pitch_summary)

        if statcast_available and game_date:
            # Charts: only regenerated if stale
            generate_pitch_charts(df, starter, game_date)

            sc_key = f"statcast_{game_date}"
            sc = statcast_cache.get(sc_key)
            if sc is None:
                sc = safe_pybaseball_call(pyb_statcast, game_date, game_date)
            if sc is not None and not sc.empty:
                generate_heatmaps(sc, starter)

    # Aggregate all other content in parallel
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
        statcast_available=statcast_available,
        pitch_mix_img="static/pitch_mix.png",
        velo_spin_img="static/velo_spin.png",
        break_chart="static/break_chart.png",
        heatmap_all_img="static/heatmap_all.png",
        heatmap_whiffs_img="static/heatmap_whiffs.png",
        heatmap_called_strikes_img="static/heatmap_called_strikes.png",
        starter=starter,
        game_date=game_date,
        opponent=opponent,
        pitch_summary=pitch_summary,
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
