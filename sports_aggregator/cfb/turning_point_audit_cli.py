"""Read-only audit of scoring/turnover EPA and WP transitions for one game."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.wp_turning_points import game_turning_points


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit CFB scoring/turnover EPA and WP attribution")
    p.add_argument("--game-id", type=int, default=None)
    p.add_argument("--team", action="append", default=[], help="Team name; pass twice to find latest matchup")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--database", default=None)
    p.add_argument("--wp-model-version", default="wp-v2")
    p.add_argument("--ep-model-version", default="ep-v1")
    return p


def _matching_games(connection, teams: list[str], season: int | None) -> list:
    """Resolve matchup using the same normalized-name semantics as the app."""
    if len(teams) != 2:
        return []
    wanted = {normalize_alias(str(team)) for team in teams}
    clauses = []
    params: list[object] = []
    if season is not None:
        clauses.append("season=?")
        params.append(int(season))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""SELECT game_id,season,week,away_team,home_team,away_points,home_points,completed
            FROM games {where}
            ORDER BY season DESC,week DESC,game_id DESC""",
        params,
    ).fetchall()
    return [
        row for row in rows
        if {normalize_alias(str(row["away_team"])), normalize_alias(str(row["home_team"]))} == wanted
    ]


def _resolve_game(connection, game_id: int | None, teams: list[str], season: int | None):
    if game_id is not None:
        return connection.execute(
            "SELECT game_id,season,week,away_team,home_team,away_points,home_points,completed FROM games WHERE game_id=?",
            (int(game_id),),
        ).fetchone(), []
    if len(teams) != 2:
        raise SystemExit("Provide --game-id or exactly two --team arguments")
    matches = _matching_games(connection, teams, season)
    if matches:
        return matches[0], matches
    # If the requested season was wrong, surface matching games from other seasons
    # instead of returning an unhelpful generic failure.
    alternatives = _matching_games(connection, teams, None) if season is not None else []
    return None, alternatives


def _home_scores(row) -> tuple[int | None, int | None]:
    try:
        offense_score = int(row["offense_score"])
        defense_score = int(row["defense_score"])
    except (TypeError, ValueError):
        return None, None
    if str(row["offense"] or "") == str(row["home_team"] or ""):
        return offense_score, defense_score
    return defense_score, offense_score


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    with closing(repository._connect()) as connection:
        game, alternatives = _resolve_game(connection, args.game_id, args.team, args.season)
        if not game:
            if alternatives:
                options = ", ".join(
                    f"game_id={row['game_id']} season={row['season']} week={row['week']} "
                    f"{row['away_team']} at {row['home_team']}"
                    for row in alternatives[:8]
                )
                raise SystemExit(f"Game not found in requested season. Matching games: {options}")
            raise SystemExit(
                "Game not found. Team matching is alias-normalized; use --game-id if you know the exact game."
            )
        game_id = int(game["game_id"])
        rows = [dict(r) for r in connection.execute("""
          SELECT p.play_id,p.period,p.clock_minutes,p.clock_seconds,p.drive_number,p.play_number,
                 p.offense,p.defense,p.home_team,p.away_team,p.play_type,p.play_text,p.scoring,
                 p.offense_score,p.defense_score,p.down,p.distance,p.yards_to_goal,p.yards_gained,
                 e.ep_before,e.ep_after,e.immediate_net_points,e.possession_changed,e.epa,
                 w.home_win_probability
          FROM cfb_plays p
          LEFT JOIN cfb_play_epa e ON e.play_id=p.play_id AND e.model_version=?
          LEFT JOIN cfb_play_win_probability w ON w.play_id=p.play_id AND w.model_version=?
          WHERE p.game_id=? AND p.period BETWEEN 1 AND 4
          ORDER BY p.period,p.clock_minutes DESC,p.clock_seconds DESC,COALESCE(p.drive_number,0),COALESCE(p.play_number,0),p.play_id
        """, (args.ep_model_version, args.wp_model_version, game_id)).fetchall()]

    for row in rows:
        hs, as_ = _home_scores(row)
        row["home_score"] = hs
        row["away_score"] = as_

    interesting: set[int] = set()
    for i, row in enumerate(rows):
        text = f"{row.get('play_type') or ''} {row.get('play_text') or ''}".casefold()
        if int(row.get("scoring") or 0) or any(k in text for k in ("touchdown", "intercept", "fumble", "safety", "field goal")):
            interesting.update(range(max(0, i - 2), min(len(rows), i + 3)))

    audit_rows = []
    previous = None
    for i in sorted(interesting):
        row = rows[i]
        item = {
            "index": i,
            "play_id": row.get("play_id"),
            "period": row.get("period"),
            "clock": f"{int(row.get('clock_minutes') or 0)}:{int(row.get('clock_seconds') or 0):02d}",
            "offense": row.get("offense"),
            "defense": row.get("defense"),
            "score": [row.get("away_score"), row.get("home_score")],
            "down": row.get("down"),
            "distance": row.get("distance"),
            "yards_to_goal": row.get("yards_to_goal"),
            "yards_gained": row.get("yards_gained"),
            "play_type": row.get("play_type"),
            "play_text": row.get("play_text"),
            "scoring": row.get("scoring"),
            "ep_before": row.get("ep_before"),
            "ep_after": row.get("ep_after"),
            "immediate_net_points": row.get("immediate_net_points"),
            "possession_changed": row.get("possession_changed"),
            "epa": row.get("epa"),
            "home_wp": row.get("home_win_probability"),
        }
        if previous is not None and row.get("home_win_probability") is not None and previous.get("home_win_probability") is not None:
            item["raw_wp_delta_from_previous_row"] = float(row["home_win_probability"]) - float(previous["home_win_probability"])
        audit_rows.append(item)
        previous = row

    output = {
        "game": dict(game),
        "models": {"ep": args.ep_model_version, "wp": args.wp_model_version},
        "published_turning_points": game_turning_points(repository, game_id, model_version=args.wp_model_version, limit=12),
        "event_neighborhood": audit_rows,
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
