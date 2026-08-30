"""Read-only audit of scoring/turnover EPA and WP transitions for one game."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import re

from dotenv import load_dotenv

from sports_aggregator.cfb.models import normalize_alias
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.wp_turning_points import game_turning_points


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit CFB scoring/turnover EPA and WP attribution")
    p.add_argument("--game-id", type=int, default=None)
    p.add_argument("--team", action="append", default=[], help="Team name; pass twice to find latest matchup")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--list-matches", action="store_true", help="List matching games and exit")
    p.add_argument("--database", default=None)
    p.add_argument("--wp-model-version", default="wp-v2")
    p.add_argument("--ep-model-version", default="ep-v1")
    return p


def _team_key(value: object) -> str:
    """Punctuation/spacing-insensitive team key for diagnostic lookup only."""
    normalized = normalize_alias(str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", normalized.casefold())


def _matches(away: object, home: object, teams: list[str]) -> bool:
    if len(teams) != 2:
        return False
    wanted = {_team_key(team) for team in teams}
    actual = {_team_key(away), _team_key(home)}
    return actual == wanted


def _involves(team: str, away: object, home: object) -> bool:
    wanted = _team_key(team)
    return wanted in {_team_key(away), _team_key(home)}


def _pbp_games(connection) -> list:
    return connection.execute("""
      SELECT p.game_id,
             MAX(p.season) AS pbp_season,
             MAX(p.week) AS pbp_week,
             MAX(p.away_team) AS pbp_away_team,
             MAX(p.home_team) AS pbp_home_team,
             COUNT(*) AS pbp_rows
      FROM cfb_plays p
      GROUP BY p.game_id
      ORDER BY MAX(p.season) DESC, MAX(p.week) DESC, p.game_id DESC
    """).fetchall()


def _enrich_candidates(connection, pbp_rows: list) -> list[dict]:
    if not pbp_rows:
        return []
    ids = [int(row["game_id"]) for row in pbp_rows]
    marks = ",".join("?" for _ in ids)
    game_rows = connection.execute(
        f"SELECT game_id,season,week,away_team,home_team,away_points,home_points,completed FROM games WHERE game_id IN ({marks})",
        ids,
    ).fetchall()
    by_id = {int(row["game_id"]): dict(row) for row in game_rows}
    output: list[dict] = []
    for row in pbp_rows:
        game_id = int(row["game_id"])
        game = by_id.get(game_id, {})
        output.append({
            "game_id": game_id,
            "season": game.get("season"),
            "week": game.get("week"),
            "away_team": game.get("away_team"),
            "home_team": game.get("home_team"),
            "away_points": game.get("away_points"),
            "home_points": game.get("home_points"),
            "completed": game.get("completed"),
            "pbp_season": row["pbp_season"],
            "pbp_week": row["pbp_week"],
            "pbp_away_team": row["pbp_away_team"],
            "pbp_home_team": row["pbp_home_team"],
            "pbp_rows": row["pbp_rows"],
        })
    return output


def _candidate_games(connection, teams: list[str]) -> list[dict]:
    """Discover exact two-team matchup IDs from PBP first."""
    if len(teams) != 2:
        return []
    rows = [row for row in _pbp_games(connection) if _matches(row["pbp_away_team"], row["pbp_home_team"], teams)]
    return _enrich_candidates(connection, rows)


def _single_team_games(connection, team: str) -> list[dict]:
    """Fallback discovery showing every PBP game involving one requested team."""
    rows = [row for row in _pbp_games(connection) if _involves(team, row["pbp_away_team"], row["pbp_home_team"])]
    return _enrich_candidates(connection, rows)


def _resolve_game(connection, game_id: int | None, teams: list[str], season: int | None):
    if game_id is not None:
        game = connection.execute(
            "SELECT game_id,season,week,away_team,home_team,away_points,home_points,completed FROM games WHERE game_id=?",
            (int(game_id),),
        ).fetchone()
        if game:
            return dict(game), []
        pbp = connection.execute("""
          SELECT game_id,MAX(season) AS pbp_season,MAX(week) AS pbp_week,
                 MAX(away_team) AS pbp_away_team,MAX(home_team) AS pbp_home_team,COUNT(*) AS pbp_rows
          FROM cfb_plays WHERE game_id=? GROUP BY game_id
        """, (int(game_id),)).fetchone()
        if pbp:
            return {
                "game_id": int(pbp["game_id"]), "season": pbp["pbp_season"], "week": pbp["pbp_week"],
                "away_team": pbp["pbp_away_team"], "home_team": pbp["pbp_home_team"],
                "away_points": None, "home_points": None, "completed": None,
            }, []
        return None, []
    if len(teams) != 2:
        raise SystemExit("Provide --game-id or exactly two --team arguments")
    candidates = _candidate_games(connection, teams)
    if season is not None:
        exact = [row for row in candidates if int(row.get("pbp_season") or row.get("season") or -1) == int(season)]
        if exact:
            return exact[0], candidates
        return None, candidates
    return (candidates[0], candidates) if candidates else (None, [])


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
        if args.list_matches:
            exact = _candidate_games(connection, args.team) if len(args.team) == 2 else []
            fallback = []
            if not exact and args.team:
                fallback = _single_team_games(connection, args.team[0])
            print(json.dumps({
                "requested_teams": args.team,
                "team_keys": [_team_key(team) for team in args.team],
                "matches": exact,
                "fallback_games_for_first_team": fallback[:30],
            }, indent=2, default=str))
            return 0
        if not game:
            fallback = _single_team_games(connection, args.team[0]) if args.team else []
            print(json.dumps({
                "requested_teams": args.team,
                "requested_season": args.season,
                "team_keys": [_team_key(team) for team in args.team],
                "matching_games": alternatives,
                "fallback_games_for_first_team": fallback[:30],
            }, indent=2, default=str))
            raise SystemExit("Game not found; discovery details printed above. Use the displayed --game-id when available.")
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
        "game": game,
        "models": {"ep": args.ep_model_version, "wp": args.wp_model_version},
        "published_turning_points": game_turning_points(repository, game_id, model_version=args.wp_model_version, limit=12),
        "event_neighborhood": audit_rows,
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
