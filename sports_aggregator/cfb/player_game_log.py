"""Player-page game log with opponent and ranking context."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any

from sports_aggregator.tables import Column, Table


PRIMARY_STATS: dict[str, tuple[str, str, str]] = {
    "QB": ("passing", "YDS", "Pass yds"),
    "RB": ("rushing", "YDS", "Rush yds"),
    "FB": ("rushing", "YDS", "Rush yds"),
    "WR": ("receiving", "YDS", "Rec yds"),
    "TE": ("receiving", "YDS", "Rec yds"),
    "K": ("kicking", "PTS", "Kick pts"),
    "P": ("punting", "AVG", "Punt avg"),
}

DEFENSIVE_POSITIONS = {
    "DE", "DT", "DL", "EDGE", "LB", "ILB", "OLB", "CB", "DB", "S", "FS", "SS", "NT",
}

FALLBACK_STATS = (
    ("passing", "YDS", "Pass yds"),
    ("rushing", "YDS", "Rush yds"),
    ("receiving", "YDS", "Rec yds"),
    ("defensive", "TOT", "Tackles"),
    ("interceptions", "INT", "INT"),
    ("kicking", "PTS", "Kick pts"),
    ("punting", "AVG", "Punt avg"),
    ("kickReturns", "YDS", "KR yds"),
    ("puntReturns", "YDS", "PR yds"),
)


def _primary_stat(connection, player_id: str, position: str | None):
    """Pick the most useful stat that actually exists anywhere in this career."""
    pos = str(position or "").upper().strip()
    preferred = ("defensive", "TOT", "Tackles") if pos in DEFENSIVE_POSITIONS else PRIMARY_STATS.get(pos)
    candidates = ([preferred] if preferred else []) + [item for item in FALLBACK_STATS if item != preferred]
    for category, stat_type, label in candidates:
        exists = connection.execute(
            """SELECT 1 FROM game_player_box_stats
               WHERE player_id=? AND category=? AND stat_type=?
               LIMIT 1""",
            (player_id, category, stat_type),
        ).fetchone()
        if exists:
            return category, stat_type, label
    return preferred or ("defensive", "TOT", "Tackles")


def _conference_rank(connection, player_id: str, season: int, conference: str | None,
                     category: str, stat_type: str) -> tuple[int | None, float | None]:
    row = connection.execute(
        """SELECT numeric_value FROM player_season_stats
           WHERE season=? AND player_id=? AND category=? AND stat_type=?
           LIMIT 1""",
        (season, player_id, category, stat_type),
    ).fetchone()
    if not row or row[0] is None or not conference:
        return None, None
    value = float(row[0])
    better = connection.execute(
        """SELECT COUNT(*) FROM player_season_stats
           WHERE season=? AND conference=? AND category=? AND stat_type=?
             AND numeric_value IS NOT NULL AND numeric_value>?""",
        (season, conference, category, stat_type, value),
    ).fetchone()[0]
    return int(better) + 1, value


def _game_rank(connection, game_id: int, category: str, stat_type: str, value: float) -> int:
    better = connection.execute(
        """SELECT COUNT(DISTINCT player_id) FROM game_player_box_stats
           WHERE game_id=? AND category=? AND stat_type=?
             AND numeric_value IS NOT NULL AND numeric_value>?""",
        (game_id, category, stat_type, value),
    ).fetchone()[0]
    return int(better) + 1


def _date_label(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%b %d")
    except ValueError:
        return str(value)[:10]


def player_game_log_table(repository, player: dict[str, Any], season: int) -> Table:
    """Career game log from stored per-game player box scores.

    The selected/current page season is not used as a row filter. Veteran player
    pages commonly point at the current roster season while their actual game
    history lives in earlier seasons.
    """
    player_id = str(player.get("player_id") or "")
    position = player.get("position")
    repository.initialize()
    with closing(repository._connect()) as connection:
        category, stat_type, stat_label = _primary_stat(connection, player_id, position)
        rows = connection.execute(
            """SELECT gp.game_id,g.season,g.week,g.start_date,
                      g.home_team_id,g.home_team,g.home_points,g.home_pregame_elo,
                      g.away_team_id,g.away_team,g.away_points,g.away_pregame_elo,
                      gp.team,gp.conference,gp.numeric_value,gp.stat_value
               FROM game_player_box_stats gp
               JOIN games g USING(game_id)
               WHERE gp.player_id=? AND gp.category=? AND gp.stat_type=?
               ORDER BY g.start_date DESC""",
            (player_id, category, stat_type),
        ).fetchall()

        elo_by_season: dict[int, dict[int, dict[str, Any]]] = {}
        rank_cache: dict[tuple[int, str | None], tuple[int | None, float | None]] = {}
        output: list[dict[str, Any]] = []
        for raw in rows:
            item = dict(raw)
            row_season = int(item["season"])
            player_is_home = item["team"] == item["home_team"]
            opponent = item["away_team"] if player_is_home else item["home_team"]
            opponent_id = item["away_team_id"] if player_is_home else item["home_team_id"]
            player_points = item["home_points"] if player_is_home else item["away_points"]
            opp_points = item["away_points"] if player_is_home else item["home_points"]
            pregame_elo = item["away_pregame_elo"] if player_is_home else item["home_pregame_elo"]
            result = "—"
            if player_points is not None and opp_points is not None:
                result = ("W" if player_points > opp_points else "L" if player_points < opp_points else "T") + f" {player_points}-{opp_points}"

            numeric = item["numeric_value"]
            display_value = numeric if numeric is not None else item["stat_value"]
            game_rank = _game_rank(connection, item["game_id"], category, stat_type, float(numeric)) if numeric is not None else None

            conference = item.get("conference")
            rank_key = (row_season, conference)
            if rank_key not in rank_cache:
                rank_cache[rank_key] = _conference_rank(
                    connection, player_id, row_season, conference, category, stat_type
                )
            conference_rank, _season_value = rank_cache[rank_key]

            if row_season not in elo_by_season:
                elo_by_season[row_season] = repository.team_elo(row_season)
            current_for_season = (elo_by_season[row_season].get(opponent_id) or {}).get("elo")
            opponent_rating = current_for_season if current_for_season is not None else pregame_elo

            output.append({
                "season": row_season,
                "week": item["week"],
                "date": _date_label(item["start_date"]),
                "opponent": opponent,
                "opponent_elo": opponent_rating,
                "result": result,
                "primary_stat": display_value,
                "game_rank": game_rank,
                "conf_rank": conference_rank,
                "game_url": f"/college-football/games/{item['game_id']}/box-score/",
            })

    note = (
        f"Career primary context: {stat_label.lower()} · opponent Elo uses the stored season rating "
        "when available, with pregame Elo as fallback"
    )
    return Table(
        columns=[
            Column("season", "Season", format="int", align="right"),
            Column("week", "Wk", format="int", align="right"),
            Column("date", "Date"),
            Column("opponent", "Opponent"),
            Column("opponent_elo", "Opp Elo", format="int", align="right"),
            Column("result", "Result"),
            Column("primary_stat", stat_label, format="num", align="right", emphasis=True),
            Column("game_rank", "Game rank", format="rank", align="right",
                   title="Rank in this game for the same stat category"),
            Column("conf_rank", "Conf rank", format="rank", align="right",
                   title="Season conference rank for the same stat"),
        ],
        rows=[{**row, "opponent_url": row["game_url"], "result_url": row["game_url"]} for row in output],
        caption="Career game log",
        note=note,
        dense=True,
        sortable=True,
        empty=(
            "No stored per-game player box scores are available for this career yet. "
            "Season totals can still appear above because they come from a separate historical dataset."
        ),
    )
