"""
charts/__init__.py — Matplotlib / Seaborn chart generation.
Charts are only regenerated when the file is stale (> 6 hours old),
avoiding redundant chart generation on every page load.
"""

import os
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from reds.utils import ensure_static_dir, chart_needs_refresh, safe_pybaseball_call
from pybaseball import statcast


def generate_pitch_charts(df: pd.DataFrame, starter: str, game_date: str) -> None:
    """Generate pitch mix, velo/spin, and break charts. Skips if files are fresh."""
    ensure_static_dir()

    charts = {
        "static/pitch_mix.png": _pitch_mix_chart,
        "static/velo_spin.png": _velo_spin_chart,
        "static/break_chart.png": _break_chart,
    }

    for path, fn in charts.items():
        if chart_needs_refresh(path):
            try:
                fn(df, starter, game_date)
            except Exception as e:
                print(f"[Charts] Failed to generate {path}: {e}")
        else:
            print(f"[Charts] Skipping {path} (still fresh)")


def _pitch_mix_chart(df: pd.DataFrame, starter: str, game_date: str) -> None:
    plt.figure(figsize=(4, 4))
    plt.pie(df["Usage %"], labels=df["Pitch Type"], autopct="%1.1f%%", startangle=140, textprops={"fontsize": 9})
    plt.title(f"{starter}'s Pitch Mix on {game_date}", fontsize=10)
    plt.tight_layout()
    plt.savefig("static/pitch_mix.png")
    plt.close()


def _velo_spin_chart(df: pd.DataFrame, starter: str, _game_date: str) -> None:
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


def _break_chart(df: pd.DataFrame, starter: str, _game_date: str) -> None:
    df = df.copy()
    df["Vertical Break (in)"]   = (df["Vertical Break"]   * 12).round(1)
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


# ---------------------------------------------------------------------------
# Heatmaps
# ---------------------------------------------------------------------------

def generate_heatmaps(statcast_df: pd.DataFrame, starter: str) -> None:
    """Generate all / called-strike / whiff heatmaps. Skips if files are fresh."""
    if statcast_df is None or statcast_df.empty:
        return
    if not {"description", "player_name"}.issubset(statcast_df.columns):
        print("[Charts] Missing required columns for heatmaps.")
        return

    charts = {
        "static/heatmap_all.png": (statcast_df, f"All Pitches — {starter}"),
        "static/heatmap_called_strikes.png": (
            statcast_df[statcast_df["description"] == "called_strike"].dropna(subset=["plate_x", "plate_z"]),
            f"Called Strikes — {starter}",
        ),
        "static/heatmap_whiffs.png": (
            statcast_df[statcast_df["description"].isin(["swinging_strike", "swinging_strike_blocked"])].dropna(subset=["plate_x", "plate_z"]),
            f"Whiffs — {starter}",
        ),
    }

    for path, (data, title) in charts.items():
        if chart_needs_refresh(path):
            _plot_heatmap(data, title, path, starter)
        else:
            print(f"[Charts] Skipping {path} (still fresh)")


def _plot_heatmap(df: pd.DataFrame, title: str, filepath: str, starter: str) -> None:
    ensure_static_dir()

    df = df.dropna(subset=["plate_x", "plate_z", "pitch_type", "player_name"]).copy()
    df["player_name"] = df["player_name"].str.title()
    starter_title = (starter or "").title()
    df = df[df["player_name"] == starter_title]

    if df.empty or df["plate_x"].nunique() < 2 or df["plate_z"].nunique() < 2:
        print(f"[Charts] Not enough data for heatmap: {title}")
        return

    avg_locations = df.groupby("pitch_type")[["plate_x", "plate_z"]].mean().reset_index()

    plt.figure(figsize=(5, 6))
    try:
        sns.kdeplot(data=df, x="plate_x", y="plate_z", fill=True, cmap="Reds", bw_adjust=0.5, levels=100, thresh=0.05)
    except Exception as e:
        print(f"[Charts] KDE plot failed: {e}")
        plt.close()
        return

    for bound in [(1.5, "h"), (3.5, "h"), (-0.83, "v"), (0.83, "v")]:
        val, axis = bound
        if axis == "h":
            plt.axhline(val, color="black", linestyle="--", linewidth=1)
        else:
            plt.axvline(val, color="black", linestyle="--", linewidth=1)

    for _, row in avg_locations.iterrows():
        plt.scatter(row["plate_x"], row["plate_z"], s=100, edgecolors="black", facecolor="white", zorder=5)
        plt.text(row["plate_x"] + 0.05, row["plate_z"] + 0.05, row["pitch_type"], fontsize=9, color="black", weight="bold", zorder=6)

    plt.title(title)
    plt.xlabel("Horizontal Location (plate_x)")
    plt.ylabel("Vertical Location (plate_z)")
    plt.tight_layout()

    try:
        plt.savefig(filepath)
        print(f"[Charts] Saved {filepath}")
    except Exception as e:
        print(f"[Charts] Failed to save {filepath}: {e}")
    plt.close()
