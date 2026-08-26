"""Turn repository packets into rendered tables.

Templates used to hold the presentation logic: which columns exist, what order
they appear in, how a percentage is formatted, and whether a result is a win.
That logic was duplicated per page and drifted. It now lives here, next to the
data it describes, and both the HTML pages and the JSON API consume the same
table objects.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from flask import url_for

from sports_aggregator.cfb.draft import position_abbreviation
from sports_aggregator.cfb.identity import conference_identity, dark_accent
from sports_aggregator.cfb.repository import _logo_pair
from sports_aggregator.cfb.statlines import (
    CATEGORY_ORDER, category_label, leader_table, player_stat_tables, sort_stat)
from sports_aggregator.tables import Column, Table, format_value


def height_label(inches: Any) -> str | None:
    """Render CFBD height inches as the feet-inches form rosters actually use."""
    try:
        total = int(inches)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return f"{total // 12}-{total % 12}"


def _team_url(team_id: Any, season: int) -> str | None:
    return url_for("cfb.team_preview", team_id=team_id) if team_id else None


def _player_url(player_id: Any, season: int) -> str | None:
    return url_for("cfb.player_preview", player_id=player_id, season=season) if player_id else None


def _game_detail_url(game: dict[str, Any]) -> str:
    endpoint = "cfb.game_box_score" if game.get("completed") else "cfb.game_preview"
    return url_for(endpoint, game_id=game["game_id"])


def brand_cell(row: dict[str, Any], key: str, brand: dict[str, Any] | None) -> None:
    """Attach logo and school color to a table cell, in place.

    Team identity is the fastest way to scan a long table, so every table that
    names a team carries its mark rather than the school name alone.
    """
    if not brand:
        return
    if brand.get("logo"):
        row[f"{key}_logo"] = brand["logo"]
        # The school's own dark-background mark, so the cell follows the theme.
        row[f"{key}_logo_dark"] = brand.get("logo_dark") or brand["logo"]
    if brand.get("color"):
        row[f"{key}_color"] = brand["color"]
        row[f"{key}_color_dark"] = dark_accent(brand["color"]) or brand["color"]


def historical_games_table(games: Sequence[dict[str, Any]], *,
                           caption: str = "Game log") -> Table:
    rows = [{
        "season": row.get("season"), "date": row.get("date_label"),
        "opponent": row.get("opponent"), "site": row.get("site"),
        "result": row.get("result"), "score": row.get("score"),
        "score_url": row.get("game_url"), "slot": row.get("slot"),
        "conference": row.get("opponent_conference"),
        "conference_conference": (conference_identity(row["opponent_conference"])
                                  if row.get("opponent_conference") else None),
        "box_score": "Box score", "box_score_url": row.get("box_score_url") or
        (f"/college-football/games/{row['game_id']}/box-score/" if row.get("game_id") else None),
    } for row in games]
    return Table(
        columns=[Column("season", "Season", format="int", align="right"),
                 Column("date", "Date"), Column("opponent", "Opponent"),
                 Column("site", "Site"), Column("result", "Result", emphasis=True),
                 Column("score", "Score", align="right"), Column("slot", "Window"),
                 Column("conference", "Opp. conf."), Column("box_score", "Detail")],
        rows=rows, caption=caption, dense=True,
        empty="No completed historical games are stored for this selection.")


def season_history_table(seasons: Sequence[dict[str, Any]]) -> Table:
    return Table(
        columns=[Column("season", "Season", format="int", align="right"),
                 Column("record", "Record", emphasis=True),
                 Column("ppg_for", "PPG", format="f1", align="right"),
                 Column("ppg_against", "Opp PPG", format="f1", align="right"),
                 Column("average_margin", "Margin", format="signed", align="right"),
                 Column("conference_wins", "Conf W", format="int", align="right"),
                 Column("conference_losses", "Conf L", format="int", align="right"),
                 Column("offense_success_rate", "Off success", format="rate", align="right",
                        title="Share of offensive plays meeting CFBD success thresholds"),
                 Column("defense_success_rate", "Def success allowed", format="rate", align="right",
                        title="Share of opponent plays meeting CFBD success thresholds; lower is better")],
        rows=seasons, caption="Season results and efficiency", dense=True,
        empty="Historical season summaries populate after the history backfill.")


def position_history_table(rows: Sequence[dict[str, Any]], *, latest_only: bool = False) -> Table:
    data = [{**row, "pff_grade_sub": (
        row.get("pff_detail") or (f"{row['pff_samples']} samples" if row.get("pff_samples") else None))}
            for row in rows]
    prefix = ([] if latest_only else [Column("season", "Season", format="int", align="right")])
    shares = ([Column("rush_yards_share", "Rush share", format="pct", align="right"),
               Column("receiving_yards_share", "Rec share", format="pct", align="right"),
               Column("tackles_share", "Tkl share", format="pct", align="right"),
               Column("sacks_share", "Sack share", format="pct", align="right")]
              if latest_only else [])
    columns = prefix + [
        Column("position_group", "Group", emphasis=True),
        Column("pass_yards", "Pass yds", format="int", align="right"),
        Column("rush_yards", "Rush yds", format="int", align="right"),
        Column("receiving_yards", "Rec yds", format="int", align="right"),
        Column("touchdowns", "TD", format="int", align="right"),
        Column("receptions", "Rec", format="int", align="right"),
        Column("tackles", "Tkl", format="f1", align="right"),
        Column("sacks", "Sacks", format="f1", align="right"),
        Column("interceptions", "INT", format="int", align="right"),
    ] + shares + [Column("pff_grade", "Top PFF", format="f1", align="right")]
    return Table(columns=columns, rows=data,
                 caption="Latest position identity" if latest_only else "Position production by season",
                 dense=True, empty="No position-level player production is stored.")


def historical_team_stats_table(rows: Sequence[dict[str, Any]]) -> Table:
    return Table(
        columns=[Column("season", "Season", format="int", align="right"),
                 Column("games", "GP", format="int", align="right"),
                 Column("yards_per_game", "YPG", format="f1", align="right"),
                 Column("pass_yards_per_game", "Pass YPG", format="f1", align="right"),
                 Column("rush_yards_per_game", "Rush YPG", format="f1", align="right"),
                 Column("opponent_yards_per_game", "Opp YPG", format="f1", align="right"),
                 Column("sacks", "Sacks", format="f1", align="right"),
                 Column("tackles_for_loss", "TFL", format="f1", align="right"),
                 Column("turnover_margin", "TO margin", format="signed", align="right")],
        rows=list(rows), caption="Traditional team production", dense=True,
        empty="Historical team-stat totals populate after the history backfill.")


def team_box_score_table(rows: Sequence[dict[str, Any]]) -> Table:
    by_team: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = by_team.setdefault(row["team"], {
            "team": row["team"], "score": row.get("points")})
        item[row["category"]] = row.get("numeric_value") if row.get("numeric_value") is not None else row.get("stat_value")
    result = []
    for item in by_team.values():
        conversions, attempts = item.get("thirdDownConversions"), item.get("thirdDowns")
        item["third_down"] = item.get("thirdDownEff")
        if item["third_down"] is None and conversions is not None and attempts is not None:
            def display(value: Any) -> str:
                try:
                    number = float(value)
                    return str(int(number)) if number.is_integer() else str(value)
                except (TypeError, ValueError):
                    return str(value)
            item["third_down"] = f"{display(conversions)}/{display(attempts)}"
        result.append(item)
    return Table(
        columns=[Column("team", "Team", emphasis=True),
                 Column("score", "Score", format="int", align="right"),
                 # Offense
                 Column("firstDowns", "1st", format="int", align="right"),
                 Column("totalYards", "Yards", format="int", align="right"),
                 Column("completionAttempts", "C/ATT", align="right"),
                 Column("netPassingYards", "Pass", format="int", align="right"),
                 Column("rushingYards", "Rush", format="int", align="right"),
                 Column("yardsPerPass", "Y/Pass", format="f1", align="right"),
                 Column("yardsPerRushAttempt", "Y/Rush", format="f1", align="right"),
                 Column("passingTDs", "Pass TD", format="int", align="right"),
                 Column("rushingTDs", "Rush TD", format="int", align="right"),
                 Column("third_down", "3rd down", align="right"),
                 Column("fourthDownEff", "4th down", align="right"),
                 Column("turnovers", "TO", format="int", align="right"),
                 Column("totalPenaltiesYards", "Pen-Yds", align="right"),
                 # Defense
                 Column("tackles", "Tkl", format="num", align="right"),
                 Column("tacklesForLoss", "TFL", format="num", align="right"),
                 Column("sacks", "Sacks", format="num", align="right"),
                 Column("qbHurries", "Hurries", format="num", align="right"),
                 Column("passesDeflected", "PD", format="num", align="right"),
                 Column("passesIntercepted", "INT", format="num", align="right"),
                 Column("fumblesRecovered", "FR", format="num", align="right"),
                 Column("defensiveTDs", "Def TD", format="num", align="right"),
                 # Special teams
                 Column("kickingPoints", "Kick pts", format="num", align="right"),
                 Column("kickReturnYards", "KR yds", format="num", align="right"),
                 Column("puntReturnYards", "PR yds", format="num", align="right"),
                 Column("possessionTime", "Possession", align="right")],
        rows=result, caption="Team box score", dense=True,
        empty="No cached team box score is stored for this game.")


def player_box_score_groups(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["team"], row["category"]), []).append(row)
    result: dict[str, list[dict[str, Any]]] = {}
    preferred_by_category = {
        "passing": ("C/ATT", "YDS", "AVG", "TD", "INT", "QBR"),
        "rushing": ("CAR", "YDS", "AVG", "TD", "LONG"),
        "receiving": ("REC", "YDS", "AVG", "TD", "LONG"),
        "defensive": ("TOT", "SOLO", "TFL", "SACKS", "QB HUR", "PD", "TD"),
        "interceptions": ("INT", "YDS", "AVG", "TD"),
        "fumbles": ("FUM", "LOST", "REC"),
        "kicking": ("FG", "PCT", "LONG", "XP", "PTS"),
        "punting": ("NO", "YDS", "AVG", "LONG", "In 20", "TB"),
        "kickReturns": ("NO", "YDS", "AVG", "LONG", "TD"),
        "puntReturns": ("NO", "YDS", "AVG", "LONG", "TD"),
    }
    category_rank = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    teams = list(dict.fromkeys(team for team, _category in grouped))
    ordered = sorted(grouped.items(), key=lambda item: (
        teams.index(item[0][0]), category_rank.get(item[0][1], 999), item[0][1]))
    for (team, category), stats in ordered:
        types = list(dict.fromkeys(row["stat_type"] for row in stats))
        preferred = preferred_by_category.get(category, ())
        types.sort(key=lambda name: (preferred.index(name) if name in preferred else 999, name))
        athletes: dict[str, dict[str, Any]] = {}
        for row in stats:
            item = athletes.setdefault(row["player_id"], {
                "player": row["player"], "player_id": row["player_id"]})
            item[row["stat_type"]] = (row.get("numeric_value")
                                      if row.get("numeric_value") is not None
                                      else row.get("stat_value"))
        athlete_rows = list(athletes.values())
        headline = sort_stat(category)
        if headline:
            athlete_rows.sort(key=lambda row: -float(row.get(headline) or 0))
        table = Table(
            columns=[Column("player", "Player", emphasis=True)] + [
                Column(name, name, format="num", align="right") for name in types],
            rows=athlete_rows, caption=f"{team} — {category_label(category)}",
            dense=True, empty="No player lines are stored for this category.")
        result.setdefault(team, []).append({"label": category_label(category),
                                             "table": table})
    return result


def opponent_performance_table(rows: Sequence[dict[str, Any]], *,
                               include_player: bool = True) -> Table:
    data = []
    for row in rows:
        data.append({
            "player": row.get("player"), "position": row.get("position"),
            "player_url": (_player_url(str(row["player_id"]), int(row["season"]))
                           if row.get("player_id") and row.get("season") else None),
            "date": row.get("date_label"), "opponent": row.get("opponent"),
            "team": row.get("team"), "passing": row.get("passing"),
            "rushing": row.get("rushing"), "receiving": row.get("receiving"),
            "defense": row.get("defense"), "detail": "Box", "detail_url": row.get("game_url"),
        })
    columns = ([Column("player", "Player", emphasis=True), Column("position", "Pos")]
               if include_player else []) + [
        Column("date", "Date"), Column("team", "Played for"),
        Column("opponent", "Opponent"), Column("passing", "Passing"),
        Column("rushing", "Rushing"), Column("receiving", "Receiving"),
        Column("defense", "Defense"), Column("detail", "Detail")]
    return Table(columns=columns, rows=data, caption="Prior games against this opponent",
                 dense=True, empty="No cached player box score against this opponent.")


def position_philosophy_table(rows: Sequence[dict[str, Any]], season: int | None) -> Table:
    """One meaningful production measure per position group plus its PFF evidence."""
    specs = {
        "QB": ("pass_yards", "Pass yards", None),
        "RB": ("rush_yards", "Rush yards", "rush_yards_share"),
        "WR": ("receiving_yards", "Receiving yards", "receiving_yards_share"),
        "TE": ("receiving_yards", "Receiving yards", "receiving_yards_share"),
        "OL": (None, "No individual production stat", None),
        "DL": ("sacks", "Sacks", "sacks_share"),
        "EDGE": ("sacks", "Sacks", "sacks_share"),
        "LB": ("tackles", "Tackles", "tackles_share"),
        "SECONDARY": ("interceptions", "Interceptions", "tackles_share"),
    }
    result = []
    for row in rows:
        group = row.get("position_group")
        if group not in specs:
            continue
        metric, label, share = specs[group]
        value = row.get(metric) if metric else None
        result.append({
            "group": group_label(group), "production": value,
            "production_sub": label, "share": row.get(share) if share else None,
            "pff_grade": row.get("pff_grade"), "pff_grade_sub": row.get("pff_detail"),
        })
    return Table(
        columns=[Column("group", "Group", emphasis=True),
                 Column("production", "Relevant production", format="big", align="right"),
                 Column("share", "Unit share", format="pct", align="right",
                        title="Share of the team's production in that statistic"),
                 Column("pff_grade", "Top PFF", format="f1", align="right",
                        title="Best stored PFF dataset grade; dataset details appear below")],
        rows=result, caption=f"{season} production identity" if season else "Position identity",
        note="Production and PFF remain separate evidence; no composite score is invented.",
        dense=True, empty="No historical position production is stored.")


# --------------------------------------------------------------------------
# Standings and schedules
# --------------------------------------------------------------------------

def standings_table(standings: Sequence[dict[str, Any]], season: int) -> Table:
    """Conference standings ordered by conference record."""
    rows = []
    for row in standings:
        logos = row.get("logos") or []
        entry = {
            "rank": row.get("rank"),
            "school": row["school"],
            "school_url": _team_url(row.get("team_id"), season),
            "school_logo": _logo_pair(logos)[0],
            "school_logo_dark": _logo_pair(logos)[1],
            "school_color": row.get("color"),
            "school_color_dark": dark_accent(row.get("color")),
            "conference_record": _record(row, "conference_wins", "conference_losses", "conference_ties"),
            "overall_record": _record(row, "wins", "losses", "ties"),
            "games": row.get("games"),
            "expected_wins": row.get("expected_wins"),
            "elo": row.get("elo"),
            "elo_sub": f"#{row['elo_rank']} FBS" if row.get("elo_rank") else None,
        }
        rows.append(entry)
    return Table(
        columns=[
            Column(key="rank", label="#", format="rank", align="right", title="Current poll rank"),
            Column(key="school", label="Team", align="left", emphasis=True),
            Column(key="conference_record", label="Conf", align="right", title="Conference record"),
            Column(key="overall_record", label="Overall", align="right", title="Overall record"),
            Column(key="games", label="GP", format="int", title="Games played"),
            Column(key="expected_wins", label="xWins", format="f1",
                   title="CFBD expected wins from game-level win probability"),
            Column(key="elo", label="Elo", format="int",
                   title="CFBD pregame Elo from the team's most recent rated game, "
                         "with its national rank among rated teams"),
        ],
        rows=rows,
        caption=f"{season} standings",
        empty="No records are stored for this conference and season.",
    )


def _record(row: dict[str, Any], wins: str, losses: str, ties: str) -> str:
    win_count, loss_count = row.get(wins) or 0, row.get(losses) or 0
    tie_count = row.get(ties) or 0
    return f"{win_count}-{loss_count}" + (f"-{tie_count}" if tie_count else "")


def schedule_table(schedule: Iterable[dict[str, Any]], team_id: int, season: int,
                   brands: dict[int, dict[str, Any]] | None = None,
                   elo: dict[int, dict[str, Any]] | None = None,
                   market: dict[int, dict[str, Any]] | None = None, *,
                   caption: str | None = None, empty: str | None = None) -> Table:
    """One team season: opponent, site, broadcast, and result in one line."""
    rows = []
    for game in schedule:
        at_home = game.get("home_team_id") == team_id
        opponent = game["away_team"] if at_home else game["home_team"]
        opponent_id = game.get("away_team_id") if at_home else game.get("home_team_id")
        team_points = game.get("home_points") if at_home else game.get("away_points")
        opponent_points = game.get("away_points") if at_home else game.get("home_points")
        result, result_class = "—", "pending"
        if game.get("completed") and team_points is not None and opponent_points is not None:
            outcome = "W" if team_points > opponent_points else "L" if team_points < opponent_points else "T"
            result = f"{outcome} {team_points}-{opponent_points}"
            result_class = {"W": "win", "L": "loss"}.get(outcome, "pending")
        entry = {
            "week": game.get("week"),
            "date": game.get("date_label") or game.get("start_label"),
            "date_sub": game.get("time_label"),
            "site": "vs" if at_home else "at",
            "opponent": opponent,
            "opponent_url": _team_url(opponent_id, season),
            "television": game.get("television"),
            "result": result,
            "result_class": result_class,
            "detail": "Box score" if game.get("completed") else "Preview",
            "detail_url": _game_detail_url(game),
        }
        # The stored spread is from the home side, so it is flipped for a road
        # game to read from this team's perspective.
        quote = (market or {}).get(game["game_id"]) or {}
        spread = quote.get("spread")
        if spread is not None and not at_home:
            spread = -spread
        opponent_elo = (elo or {}).get(opponent_id) or {}
        entry["spread"] = spread
        entry["total"] = quote.get("total")
        entry["opponent_elo"] = opponent_elo.get("elo")
        entry["opponent_elo_sub"] = (f"#{opponent_elo['elo_rank']}"
                                     if opponent_elo.get("elo_rank") else None)
        brand_cell(entry, "opponent", (brands or {}).get(opponent_id))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="week", label="Wk", format="rank", align="right"),
            Column(key="date", label="Date", align="left"),
            Column(key="site", label="", align="right", title="Home or away"),
            Column(key="opponent", label="Opponent", align="left", emphasis=True),
            Column(key="opponent_elo", label="Opp Elo", format="int",
                   title="CFBD pregame Elo for the opponent, with its national rank"),
            Column(key="spread", label="Line", format="signed",
                   title="Consensus spread from this team's perspective, where books have posted one"),
            Column(key="total", label="O/U", format="f1", title="Consensus total"),
            Column(key="television", label="TV", align="left"),
            Column(key="result", label="Result", align="right"),
            Column(key="detail", label="", align="right"),
        ],
        rows=rows,
        caption=caption or f"{season} schedule",
        empty=empty or f"No {season} schedule is stored.",
    )


def games_table(games: Iterable[dict[str, Any]], caption: str,
                brands: dict[int, dict[str, Any]] | None = None,
                elo: dict[int, dict[str, Any]] | None = None) -> Table:
    """Upcoming games, with each team carrying its own mark and color."""
    rows = []
    for game in games:
        entry = {
            "week": game.get("week"),
            "date": game.get("date_label") or game.get("start_label"),
            "date_sub": game.get("time_label"),
            "away_team": game["away_team"],
            "home_team": game["home_team"],
            "television": game.get("television"),
            "venue": game.get("venue"),
            "detail": "Box score" if game.get("completed") else "Preview",
            "detail_url": _game_detail_url(game),
        }
        entry["away_elo"] = ((elo or {}).get(game.get("away_team_id")) or {}).get("elo")
        entry["home_elo"] = ((elo or {}).get(game.get("home_team_id")) or {}).get("elo")
        brand_cell(entry, "away_team", (brands or {}).get(game.get("away_team_id")))
        brand_cell(entry, "home_team", (brands or {}).get(game.get("home_team_id")))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="week", label="Wk", format="rank", align="right"),
            Column(key="date", label="Kickoff", align="left"),
            Column(key="away_team", label="Away", align="left", emphasis=True),
            Column(key="away_elo", label="Elo", format="int", title="CFBD pregame Elo"),
            Column(key="home_team", label="Home", align="left", emphasis=True),
            Column(key="home_elo", label="Elo", format="int", title="CFBD pregame Elo"),
            Column(key="television", label="TV", align="left"),
            Column(key="detail", label="", align="right"),
        ],
        rows=rows,
        caption=caption,
        empty="No future games are stored for this season.",
    )


# --------------------------------------------------------------------------
# Player production
# --------------------------------------------------------------------------

def leader_groups(leaders: dict[str, Any], season: int, *,
                  include_team: bool = True, limit: int | None = None) -> list[dict[str, Any]]:
    """Leaderboards as tabbed tables, each showing the full category stat line."""
    groups = []
    for category, group in leaders.get("groups", {}).items():
        table = leader_table(category, group["players"], include_team=include_team, limit=limit)
        origins = {entry.get("player_id"): entry.get("origin")
                   for entry in group["players"] if entry.get("arrival")}
        for row in table.rows:
            row["player_url"] = _player_url(row.get("player_id"), season)
            origin = origins.get(row.get("player_id"))
            if origin:
                # Production earned at another school is never shown unlabelled.
                row["player_sub"] = f"arrived from {origin}"
                row["player_class"] = "state-arrived"
        note = f"Ranked by {group['stat_type']}"
        if group.get("qualifier"):
            note += f" · {group['qualifier']}"
        table.note = note
        groups.append({"category": category, "label": group["label"], "table": table})
    return groups


def player_stat_groups(player: dict[str, Any]) -> list[dict[str, Any]]:
    """Career stat lines, pivoted out of the long-form statistics store."""
    return player_stat_tables(player.get("stats") or [])


def _pff_detail(grade: dict[str, Any]) -> str | None:
    """A compact, dataset-specific stat line from the licensed raw row."""
    try:
        raw = json.loads(grade.get("metrics_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None

    def number(key: str, digits: int = 0) -> str | None:
        value = raw.get(key)
        if value in (None, ""):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        return f"{numeric:.{digits}f}" if digits else f"{numeric:,.0f}"

    dataset = grade.get("dataset")
    if dataset in {"blocking", "blocking_history"}:
        return " · ".join(filter(None, (
            f"PB {number('grades_pass_block', 1)}" if number("grades_pass_block", 1) else None,
            f"RB {number('grades_run_block', 1)}" if number("grades_run_block", 1) else None,
            f"{number('pressures_allowed')} pressures allowed" if number("pressures_allowed") else None,
        ))) or None
    if dataset == "receiving":
        routes = number("routes")
        yards = number("yards")
        yprr = None
        try:
            if float(raw.get("routes") or 0) > 0:
                yprr = float(raw.get("yards") or 0) / float(raw["routes"])
        except (TypeError, ValueError):
            pass
        return " · ".join(filter(None, (
            f"{number('targets')} tgt / {number('receptions')} rec" if number("targets") else None,
            f"{yards} yd" if yards else None, f"{yprr:.2f} YPRR" if yprr is not None else None,
        ))) or None
    if dataset == "rushing":
        return " · ".join(filter(None, (
            f"{number('attempts')} att / {number('yards')} yd" if number("attempts") else None,
            f"receiving {number('targets')} tgt, {number('receptions')} rec, {number('rec_yards')} yd"
            if number("targets") else None,
        ))) or None
    if dataset in {"coverage", "coverage_scheme"}:
        if dataset == "coverage_scheme":
            return " · ".join(filter(None, (
                f"man {number('man_snap_counts_coverage')} snaps / {number('man_grades_coverage_defense', 1)} grade"
                if number("man_snap_counts_coverage") else None,
                f"zone {number('zone_snap_counts_coverage')} snaps / {number('zone_grades_coverage_defense', 1)} grade"
                if number("zone_snap_counts_coverage") else None,
            ))) or None
        return " · ".join(filter(None, (
            f"{number('targets')} tgt / {number('receptions')} rec" if number("targets") else None,
            f"{number('yards')} yd allowed" if number("yards") else None,
            f"{number('qb_rating_against', 1)} rating" if number("qb_rating_against", 1) else None,
        ))) or None
    if dataset == "pass_rush":
        return " · ".join(filter(None, (
            f"{number('total_pressures')} pressures" if number("total_pressures") else None,
            f"{number('sacks', 1)} sacks" if number("sacks", 1) else None,
            f"{number('pass_rush_win_rate', 1)}% win" if number("pass_rush_win_rate", 1) else None,
        ))) or None
    if dataset == "receiving_scheme":
        return " · ".join(filter(None, (
            f"man {number('man_routes')} routes / {number('man_yards')} yd / {number('man_grades_pass_route', 1)} grade"
            if number("man_routes") else None,
            f"zone {number('zone_routes')} routes / {number('zone_yards')} yd / {number('zone_grades_pass_route', 1)} grade"
            if number("zone_routes") else None,
        ))) or None
    if dataset == "passing_depth":
        return " · ".join(filter(None, (
            f"deep {number('deep_attempts')} att / {number('deep_ypa', 1)} YPA"
            if number("deep_attempts") else None,
            f"medium {number('medium_attempts')} att / {number('medium_ypa', 1)} YPA"
            if number("medium_attempts") else None,
        ))) or None
    if dataset == "returns":
        return " · ".join(filter(None, (
            f"{number('kickoff_attempts')} KR / {number('kickoff_yards')} yd" if number("kickoff_attempts") else None,
            f"{number('punt_attempts')} PR / {number('punt_yards')} yd" if number("punt_attempts") else None,
        ))) or None
    if dataset == "run_defense_detail":
        return " · ".join(filter(None, (
            f"{number('snap_counts_run')} run snaps" if number("snap_counts_run") else None,
            f"{number('run_stop_percent', 1)}% stops" if number("run_stop_percent", 1) else None,
        ))) or None
    return None


def pff_grades_table(grades: Sequence[dict[str, Any]]) -> Table:
    """Confirmed PFF grades for one player, with the usage that qualifies them."""
    rows = [{
        "season": grade.get("season"),
        "dataset": (grade.get("dataset") or "overall").replace("_", " ").title(),
        "dataset_sub": ("Regular-season scheme/depth split"
                        if grade.get("context") == "REGULAR_SEASON_DETAIL"
                        else "Regular-season college sample"),
        "primary_grade": grade.get("primary_grade"),
        "usage_count": grade.get("usage_count"),
        "games": grade.get("games"),
        "detail": _pff_detail(grade),
    } for grade in grades]
    return Table(
        columns=[
            Column(key="season", label="Season", format="rank", align="left"),
            Column(key="dataset", label="Dataset", align="left"),
            Column(key="primary_grade", label="Grade", format="f1", emphasis=True,
                   title="Dataset-specific PFF primary grade"),
            Column(key="usage_count", label="Usage", format="num",
                   title="Snaps, attempts, or routes for this dataset"),
            Column(key="games", label="G", format="int", title="Games"),
            Column(key="detail", label="Detail", align="left"),
        ],
        rows=rows,
        caption="PFF grades",
        empty="No confirmed PFF-to-CFBD identity link is stored.",
    )


def pff_players_table(players: Sequence[dict[str, Any]], season: int, *,
                      caption: str = "Players to know", dense: bool = False) -> Table:
    """Historical PFF players with their current roster status made explicit."""
    rows = []
    for player in players:
        identifier = player.get("player_page_id") or player.get("cfbd_player_id")
        rows.append({
            "player_name": player.get("player_name"),
            "player_name_url": _player_url(identifier, season),
            "position": player.get("position"),
            "team": player.get("cfbd_team") or player.get("pff_team_name"),
            "roster_status": (player.get("roster_status") or "").replace("_", " ").title() or None,
            "roster_status_sub": player.get("roster_destination"),
            "interest_score": player.get("interest_score"),
        })
    return Table(
        columns=[
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="team", label="Team", align="left"),
            Column(key="roster_status", label="Status", align="left"),
            Column(key="interest_score", label="Interest", format="f1",
                   title="Application interest score; discounts small samples"),
        ],
        rows=rows,
        caption=caption,
        note="2025 snapshot",
        empty="No linked historical PFF players.",
        dense=dense,
    )


#: Position abbreviations that title-casing would otherwise mangle into "Qb".
POSITION_ABBREVIATIONS = frozenset({
    "QB", "RB", "HB", "FB", "WR", "TE", "OL", "DL", "LB", "DB", "EDGE",
    "IDL", "IOL", "CB", "S", "K", "P", "LS", "ST",
})


def group_label(value: str | None) -> str | None:
    """Title-case a position-group name without lowercasing its abbreviations."""
    if not value:
        return None
    words = []
    for word in value.replace("_", " ").split():
        upper = word.upper()
        words.append(upper if upper in POSITION_ABBREVIATIONS else word.title())
    return " ".join(words)


def notable_arrivals_table(arrivals, season, *, caption="Arrived"):
    """Portal additions with production, rendered like the other player tables.

    Arrivals previously sat in a bespoke card list with no links, so a reader
    could not reach the player from the one section most likely to make them
    curious. This uses the same table contract as returning and departed
    players so the three read as one comparison.
    """
    rows = []
    for row in arrivals:
        rows.append({
            "player_name": row.get("player_name"),
            "player_name_url": _player_url(row.get("player_id") or row.get("cfbd_player_id"), season),
            "player_name_class": "state-arrived",
            "position": row.get("position"),
            "origin": row.get("origin"),
            "impact": row.get("impact_label"),
            "impact_sub": (row.get("reasons") or [None])[0],
            "impact_score": row.get("impact_score") if row.get("has_evidence") else None,
        })
    return Table(
        columns=[
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="origin", label="From", align="left"),
            Column(key="impact", label="Read", align="left"),
            Column(key="impact_score", label="Impact", format="f1",
                   title="Prior production first, then grade, then recruiting opinion"),
        ],
        rows=rows,
        caption=caption,
        note="portal additions with a record",
        empty="No portal additions with a production record.",
        dense=True,
    )


def pff_departures_table(players: Sequence[dict[str, Any]], season: int, *,
                         caption: str = "Key departures") -> Table:
    """Graded players who have left, with where each went.

    Departures and returners answer different questions -- who is gone, and who is
    still here -- so mixing them behind a status column made both harder to read.
    """
    rows = []
    for player in players:
        identifier = player.get("player_page_id") or player.get("cfbd_player_id")
        status = (player.get("roster_status") or "").replace("_", " ").title()
        rows.append({
            "player_name": player.get("player_name"),
            "player_name_url": _player_url(identifier, season),
            "position": player.get("position"),
            "status": status or "Unresolved",
            "destination": player.get("roster_destination"),
            "interest_score": player.get("interest_score"),
        })
    return Table(
        columns=[
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="status", label="Left via", align="left"),
            Column(key="destination", label="To", align="left"),
            Column(key="interest_score", label="PFF", format="f1",
                   title="2025 PFF interest score"),
        ],
        rows=rows,
        caption=caption,
        note="2025 snapshot",
        empty="No graded departures identified.",
        dense=True,
    )


def unit_continuity_table(units: Sequence[dict[str, Any]], season: int) -> Table:
    """Last season's unit grades beside how much of each unit actually returns.

    A grade with no continuity beside it invites the reader to assume the unit
    is intact. Snaps back is shown next to the share because 5% of 1,763 snaps
    and 5% of 60 are very different claims.
    """
    rows = []
    for unit in sorted(units, key=lambda item: -(item.get("continuity_usage") or 0)):
        share = unit.get("returning_share")
        rows.append({
            "unit": group_label(unit.get("position_group")),
            "unit_sub": (unit.get("dataset") or "").replace("_", " ").title(),
            "prior_grade": unit.get("prior_grade"),
            "returning": None if share is None else round(share * 100, 1),
            "snaps_back": unit.get("returning_usage"),
            "snaps_back_sub": (f"of {unit['continuity_usage']:,.0f}"
                               if unit.get("continuity_usage") else None),
            "adjusted": unit.get("blended_grade"),
            "adjusted_sub": unit.get("blend_basis"),
        })
    return Table(
        columns=[
            Column(key="unit", label="Unit", align="left", emphasis=True),
            Column(key="prior_grade", label=f"{season} grade", format="f1",
                   title="Usage-weighted PFF grade for the group"),
            Column(key="returning", label="Returning", format="pct",
                   title="Share of the snaps behind that grade that are on this "
                         "year's roster"),
            Column(key="snaps_back", label="Snaps back", format="num"),
            Column(key="adjusted", label="Adjusted", format="f1",
                   title="Prior grade weighted against current-season play by how "
                         "much of the unit returned"),
        ],
        rows=rows,
        caption="Unit grades and continuity",
        note=(f"{season} usage-weighted. Adjusted equals the {season} grade until "
              "this season's own grades exist, then shifts toward current play at "
              "a rate set by how much of the unit returned."),
        empty="No qualifying unit rollups are stored for this team.",
        dense=True,
    )


def pff_position_groups_table(groups: Sequence[dict[str, Any]]) -> Table:
    """Usage-weighted position-group grades."""
    rows = [{
        "position_group": group_label(group.get("position_group")),
        "dataset": (group.get("dataset") or "").replace("_", " ").title(),
        "player_count": group.get("player_count"),
        "weighted_grade": group.get("weighted_grade"),
    } for group in groups]
    return Table(
        columns=[
            Column(key="position_group", label="Group", align="left", emphasis=True),
            Column(key="dataset", label="Dataset", align="left"),
            Column(key="player_count", label="Players", format="int"),
            Column(key="weighted_grade", label="Grade", format="f1",
                   title="Usage-weighted PFF grade for the group"),
        ],
        rows=rows,
        caption="Position groups",
        note="2025 usage-weighted",
        empty="No qualifying position-group rollups.",
        dense=True,
    )


# --------------------------------------------------------------------------
# Roster construction
# --------------------------------------------------------------------------

def signing_class_table(signing: dict[str, Any]) -> Table:
    """A signing class, best first, with what each signee brings.

    The class summary sits in the caption rather than in a separate panel: a
    reader wants the headline and the names in one glance, not two blocks
    separated by whitespace.
    """
    rows = [{
        "name": row.get("name"),
        "position": row.get("position"),
        "stars": row.get("stars"),
        "rating": row.get("rating"),
        "ranking": row.get("ranking"),
        "home": " ".join(part for part in (row.get("home_city"),
                                           row.get("home_state")) if part) or None,
    } for row in (signing.get("signees") or [])]

    rank = signing.get("national_rank")
    points = signing.get("points")
    note_parts = []
    if rank and signing.get("classes_ranked"):
        note_parts.append(f"#{rank} of {signing['classes_ranked']} classes")
    if points is not None:
        note_parts.append(f"{points:g} pts from the top "
                          f"{signing.get('scored_class_size', 20)}")
    counts = signing.get("counts") or {}
    tiers = " · ".join(f"{counts[key]} {key}" for key in
                       ("5-star", "4-star", "3-star") if counts.get(key))
    if tiers:
        note_parts.append(tiers)

    return Table(
        columns=[
            Column(key="name", label="Signee", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="stars", label="★", format="int",
                   title="Recruiting stars"),
            Column(key="rating", label="Rating", format="f3",
                   title="CFBD composite rating"),
            Column(key="ranking", label="Natl", format="rank", align="right",
                   title="National ranking within the class"),
            Column(key="home", label="From", align="left"),
        ],
        rows=rows,
        caption=f"{signing.get('season')} signing class ({signing.get('signee_count', 0)})",
        note="; ".join(note_parts) or None,
        empty="No signing class is stored for this team.",
        dense=True,
    )


def _recruit_summary(player: dict[str, Any]) -> str | None:
    """What a player with no college record brings instead.

    Previously "N-star signee" and nothing else, which said no more than the
    class-year column already did. The national ranking is the part that
    distinguishes the tenth-best recruit in the country from the four hundredth.
    """
    stars = player.get("recruit_stars")
    if not stars:
        return None
    rating = player.get("recruit_rating")
    detail = f"{stars}-star"
    if rating:
        detail += f" · {rating:.3f}"
    return detail


def depth_chart_tables(depth_chart: dict[str, Any], season: int,
                       projection: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Position groups as tables so class, size, and origin line up per player."""
    units = []
    for unit, groups in (depth_chart.get("units") or {}).items():
        tables = []
        for group, players in groups.items():
            rows = []
            for player in players:
                if player.get("arrival_type") == "TRANSFER_IN":
                    origin = f"Transfer from {player.get('origin') or 'unknown'}"
                elif player.get("is_returner"):
                    origin = "Returner"
                else:
                    origin = "New"
                evidence = (projection or {}).get(player.get("player_id")) or {}
                rows.append({
                    "jersey": player.get("jersey"),
                    "name": player.get("name"),
                    "name_url": _player_url(player.get("player_id"), season),
                    "name_class": ("state-arrived" if evidence.get("state") == "ARRIVED"
                                   else None),
                    "class_year": player.get("class_year"),
                    "height": height_label(player.get("height")),
                    "weight": player.get("weight"),
                    "origin": origin,
                    "production": (evidence.get("summary")
                                    or _recruit_summary(player)),
                    "pff_interest": player.get("pff_interest"),
                    "pff_interest_sub": (player.get("pff_graded_at")
                                         if player.get("pff_graded_at")
                                         and player.get("arrival_type") else None),
                })
            tables.append({
                "label": f"{group} ({len(players)})",
                "table": Table(
                    columns=[
                        Column(key="jersey", label="#", format="rank", align="right"),
                        Column(key="name", label="Player", align="left", emphasis=True),
                        Column(key="class_year", label="Cl", format="rank", align="right",
                               title="Class year, 1 through 4"),
                        Column(key="height", label="Ht", align="right"),
                        Column(key="weight", label="Wt", format="int", title="Pounds"),
                        Column(key="origin", label="Origin", align="left"),
                        Column(key="production", label="Prior production", align="left",
                               title="Last season's headline production, and where it was earned"),
                        Column(key="pff_interest", label="PFF", format="f1",
                               title="2025 PFF interest score, and the school it "
                                     "was earned at when that differs"),
                    ],
                    rows=rows,
                    caption=group,
                    dense=True,
                    empty="No players in this group.",
                ),
            })
        units.append({"unit": unit, "groups": tables})
    return units


def movements_table(rows: Sequence[dict[str, Any]], season: int, *, arrivals: bool) -> Table:
    """Arrivals or departures with the evidence for each classification."""
    direction_key = "origin" if arrivals else "destination"
    entries = [{
        "name": row.get("name"),
        "name_url": _player_url(row.get("player_id"), season),
        "position": row.get("position"),
        "movement_type": (row.get("movement_type") or "").replace("_", " ").title(),
        "counterparty": row.get(direction_key),
        "evidence": row.get("evidence"),
    } for row in rows]
    return Table(
        columns=[
            Column(key="name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="movement_type", label="Type", align="left"),
            Column(key="counterparty", label="From" if arrivals else "To", align="left"),
            Column(key="evidence", label="Evidence", align="left",
                   title="Why this player was classified this way"),
        ],
        rows=entries,
        caption="Arrivals" if arrivals else "Departures",
        empty="No arrivals identified." if arrivals else "No departures identified.",
        dense=True,
    )


def movement_stream_table(events: Sequence[dict[str, Any]]) -> Table:
    """Portal and draft events as one dated table rather than mixed cards."""
    rows = []
    for event in events:
        if event.get("event_type") == "DRAFTED":
            destination = event.get("nfl_team")
            detail = f"Round {event.get('round')}, pick {event.get('overall_pick')}"
        else:
            destination = event.get("destination") or "TBD"
            detail = f"{event.get('stars') or '—'} star" if event.get("stars") else None
        rows.append({
            "event_type": (event.get("event_type") or "").title(),
            "player_name": event.get("player_name"),
            "position": event.get("position"),
            "origin": event.get("origin"),
            "destination": destination,
            "destination_sub": detail,
            "rating": event.get("rating"),
        })
    return Table(
        columns=[
            Column(key="event_type", label="Event", align="left"),
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="origin", label="From", align="left"),
            Column(key="destination", label="To", align="left"),
            Column(key="rating", label="Rating", format="f2",
                   title="CFBD transfer rating where published"),
        ],
        rows=rows,
        caption="Personnel movement",
        empty="Portal and draft data have not been synchronized.",
    )


# --------------------------------------------------------------------------
# Team quality
# --------------------------------------------------------------------------

#: (row label, offense column, defense column, format) for advanced metrics.
ADVANCED_METRICS = (
    ("Success rate", "offense_success_rate", "defense_success_rate", "rate"),
    ("Explosiveness", "offense_explosiveness", "defense_explosiveness", "f2"),
    ("PPA per play", "offense_ppa", "defense_ppa", "f3"),
    ("Points per opportunity", "offense_points_per_opportunity",
     "defense_points_per_opportunity", "f2"),
    ("Havoc", "offense_havoc", "defense_havoc", "rate"),
)


def _scale_visual(gap: float, left_wins: bool, *,
                  full_scale_gap: float = 30.0) -> dict[str, float]:
    offset = min(45.0, gap / full_scale_gap * 45.0)
    position = 50.0 - offset if left_wins else 50.0 + offset
    return {
        "gap": gap,
        "position": position,
        "left": min(50.0, position),
        "width": abs(position - 50.0),
    }


def _comparison_visual(left: Any, right: Any, left_wins: bool, *,
                       full_scale_gap: float = 30.0) -> dict[str, float]:
    """Place a comparison on a compact, symmetric left/right scale.

    The percentage gap uses the mean magnitude as its denominator so swapping
    the teams never changes the displayed separation. The visual saturates at
    ``full_scale_gap`` to keep extreme outliers from distorting every row.
    """
    left_value, right_value = float(left), float(right)
    baseline = max((abs(left_value) + abs(right_value)) / 2, 1.0)
    gap = abs(left_value - right_value) / baseline * 100
    return _scale_visual(gap, left_wins, full_scale_gap=full_scale_gap)


def matchup_metrics_table(game: dict[str, Any]) -> Table:
    """Advanced metrics for both teams, one metric per row.

    Each metric keeps its own format, so a rate is not displayed with the same
    three decimal places as a PPA figure.
    """
    metrics = game.get("advanced_metrics") or {}
    home = metrics.get(game["home_team"], {})
    away = metrics.get(game["away_team"], {})
    rows = [{
        "metric": label,
        "away_offense": away.get(offense), "away_defense": away.get(defense),
        "home_offense": home.get(offense), "home_defense": home.get(defense),
        "format": fmt,
    } for label, offense, defense, fmt in ADVANCED_METRICS]
    # A single column format cannot describe rows that mix rates with per-play
    # figures, so each value is pre-rendered against its own row format.
    for row in rows:
        for key in ("away_offense", "away_defense", "home_offense", "home_defense"):
            row[key] = format_value(row[key], row["format"])
    return Table(
        columns=[
            Column(key="metric", label="Metric", align="left", emphasis=True),
            Column(key="away_offense", label=f"{game['away_team']} O", align="right"),
            Column(key="away_defense", label=f"{game['away_team']} D", align="right"),
            Column(key="home_offense", label=f"{game['home_team']} O", align="right"),
            Column(key="home_defense", label=f"{game['home_team']} D", align="right"),
        ],
        rows=rows,
        caption=f"{game['season']} advanced metrics",
        empty="No advanced metrics are stored; they populate once plays are recorded.",
    )


def matchup_watch_table(report: dict[str, Any], brands_by_school: dict[str, dict[str, Any]] | None = None,
                        limit: int | None = None) -> Table:
    """Ranked unit matchups, most watchable first, with the reason attached."""
    rows = []
    for item in (report.get("matchups") or [])[:limit] if limit else report.get("matchups") or []:
        entry = {
            "label": item["label"],
            "label_sub": item["headline"],
            "attack": f"{item['attack_team']} {item['attack_label'].lower()}",
            "attack_grade": item["attack_grade"],
            "defend": f"{item['defend_team']} {item['defend_label'].lower()}",
            "defend_grade": item["defend_grade"],
            "margin": item["margin"],
            "advantage": item["advantage"] or "Even",
            "interest": item["interest"],
            "interest_sub": None if item["confident"] else "limited sample",
        }
        brand_cell(entry, "attack", (brands_by_school or {}).get(item["attack_team"]))
        brand_cell(entry, "defend", (brands_by_school or {}).get(item["defend_team"]))
        brand_cell(entry, "advantage", (brands_by_school or {}).get(item["advantage"]))
        if item["advantage"]:
            attack_wins = item["advantage"] == item["attack_team"]
            visual = _comparison_visual(item["attack_grade"], item["defend_grade"],
                                        attack_wins, full_scale_gap=20.0)
            winner_key = "attack_grade" if attack_wins else "defend_grade"
            entry[f"{winner_key}_class"] = "advantage"
            entry[f"{winner_key}_sub"] = f"{item['margin']:.1f}-point edge"
            entry["advantage_scale_position"] = f"{visual['position']:.1f}"
            entry["advantage_scale_left"] = f"{visual['left']:.1f}"
            entry["advantage_scale_width"] = f"{visual['width']:.1f}"
            entry["advantage_scale_label"] = (
                f"{item['attack_team']} attack on the left, "
                f"{item['defend_team']} defense on the right; "
                f"{item['advantage']} holds a {item['margin']:.1f}-point edge"
            )
        rows.append(entry)
    return Table(
        columns=[
            Column(key="label", label="Matchup", align="left", emphasis=True),
            Column(key="attack", label="Attacking unit", align="left"),
            Column(key="attack_grade", label="Grade", format="f1"),
            Column(key="defend", label="Defending unit", align="left"),
            Column(key="defend_grade", label="Grade", format="f1"),
            Column(key="advantage", label="Edge", align="left"),
            Column(key="interest", label="Watch", format="f1",
                   title="How interesting this matchup is: quality, separation, and mutual strength"),
        ],
        rows=rows,
        caption="Matchups to watch",
        note="2025 PFF grades",
        empty="No qualifying graded unit comparison is available for these teams.",
    )


def player_matchup_table(matchups: Sequence[dict[str, Any]], season: int) -> Table:
    """Credible player pairings blended with player-vs-unit watches."""
    rows = []
    for matchup in matchups:
        attacker, defender = matchup["attacker"], matchup["defender"]
        members = defender.get("members") or []
        defender_detail = (", ".join(
            f"{member['player_name']} {member['grade']:.1f}" for member in members)
            if members else None)
        entry = {
            "label": matchup["label"],
            "label_sub": matchup["why"],
            "attacker": attacker["player_name"],
            "attacker_url": _player_url(attacker.get("cfbd_player_id"), season),
            "attacker_sub": (f"{attacker['school']} {attacker['position']}"
                             + (f" · board #{attacker['board_rank']}"
                                if attacker.get("board_rank") else "")),
            "attacker_grade": attacker.get("interest_score"),
            "defender": defender["player_name"],
            "defender_url": _player_url(defender.get("cfbd_player_id"), season),
            "defender_sub": (defender_detail or
                             (f"{defender['school']} {defender['position']}"
                              + (f" · board #{defender['board_rank']}"
                                 if defender.get("board_rank") else ""))),
            "defender_grade": defender.get("interest_score"),
            "interest": matchup["interest"],
        }
        if attacker.get("accent"):
            entry["attacker_color"] = attacker["accent"]
            entry["attacker_color_dark"] = attacker.get("accent_dark") or attacker["accent"]
        if defender.get("accent"):
            entry["defender_color"] = defender["accent"]
            entry["defender_color_dark"] = defender.get("accent_dark") or defender["accent"]
        rows.append(entry)
    return Table(
        columns=[
            Column(key="label", label="Matchup", align="left", emphasis=True),
            Column(key="attacker", label="Player", align="left", emphasis=True),
            Column(key="attacker_grade", label="PFF", format="f1"),
            Column(key="defender", label="Against", align="left", emphasis=True),
            Column(key="defender_grade", label="PFF", format="f1"),
            Column(key="interest", label="Watch", format="f1",
                   title="Both players graded well; ranked draft prospects raise it further"),
        ],
        rows=rows,
        caption="Player and unit watches",
        note="2025 PFF grades · unit rows list the leading graded members",
        empty="No graded player or unit watch is available for these rosters.",
    )


def weekly_matchups_table(matchups: Sequence[dict[str, Any]], season: int) -> Table:
    """Top blended watches across the nearest upcoming week."""
    rows = []
    for matchup in matchups:
        rows.append({
            "game": f"{matchup['away_team']} at {matchup['home_team']}",
            "game_url": url_for("cfb.game_preview", game_id=matchup["game_id"]),
            "game_sub": matchup.get("start_label"),
            "kind": matchup.get("kind_label"), "focus": matchup.get("focus"),
            "focus_url": _player_url(matchup.get("focus_player_id"), season),
            "focus_sub": matchup.get("label"), "against": matchup.get("against"),
            "against_sub": matchup.get("detail"),
            "watch_score": matchup.get("weekly_score"),
        })
    return Table(
        columns=[
            Column(key="game", label="Game", align="left", emphasis=True),
            Column(key="kind", label="Type", align="left"),
            Column(key="focus", label="Focus", align="left", emphasis=True),
            Column(key="against", label="Against", align="left"),
            Column(key="watch_score", label="Watch", format="f1",
                   title="Matchup interest blended with the game's attention score"),
        ], rows=rows, caption="Top matchups in the next scheduled week",
        note="player-v-player, player-v-unit, and unit-v-unit",
        empty="No qualifying graded matchup is available for the upcoming week.",
    )


def _share_percent(share: float | None) -> float | None:
    return None if share is None else round(share * 100, 1)


def pff_units_table(units: Sequence[dict[str, Any]], away_team: str, home_team: str) -> Table:
    """Side-by-side unit grades for the two teams in a game."""
    rows = [{
        "label": unit.get("label"),
        "away_grade": unit.get("away_grade"),
        "away_returning": _share_percent(unit.get("away_returning_share")),
        "away_usage": unit.get("away_usage"),
        "home_grade": unit.get("home_grade"),
        "home_returning": _share_percent(unit.get("home_returning_share")),
        "home_usage": unit.get("home_usage"),
    } for unit in units]
    return Table(
        columns=[
            Column(key="label", label="Unit", align="left", emphasis=True),
            Column(key="away_grade", label=f"{away_team} grade", format="f1"),
            Column(key="away_returning", label="Ret.", format="pct",
                   title=f"Share of the graded snaps {away_team} returns"),
            Column(key="away_usage", label="Usage", format="num"),
            Column(key="home_grade", label=f"{home_team} grade", format="f1"),
            Column(key="home_returning", label="Ret.", format="pct",
                   title=f"Share of the graded snaps {home_team} returns"),
            Column(key="home_usage", label="Usage", format="num"),
        ],
        rows=rows,
        caption="Unit grades",
        note="2025 PFF usage-weighted, with the share of those snaps still on each roster",
        empty="No unit grades are stored for these teams.",
    )


def preseason_context_table(away_team: str, away_quality: dict[str, Any],
                            home_team: str, home_quality: dict[str, Any]) -> Table:
    """Preseason context signals side by side, labeled as context, not projection."""
    away_cards = away_quality.get("cards") or []
    home_cards = home_quality.get("cards") or []
    rows = []
    for index, card in enumerate(away_cards):
        counterpart = home_cards[index] if index < len(home_cards) else {}
        fmt = card.get("format") or "num"
        rows.append({
            "label": card.get("label"),
            "label_sub": card.get("source"),
            "away": format_value(card.get("value"), fmt),
            "home": format_value(counterpart.get("value"), fmt),
        })
    return Table(
        columns=[
            Column(key="label", label="Preseason context", align="left", emphasis=True),
            Column(key="away", label=away_team, align="right"),
            Column(key="home", label=home_team, align="right"),
        ],
        rows=rows,
        caption="Preseason context",
        note="Context signals, not a predictive composite",
        empty="No preseason context signals are stored for these teams.",
    )


def quality_cards_table(quality: dict[str, Any]) -> Table:
    """One team version of the preseason context signals."""
    rows = [{
        "label": card.get("label"),
        "value": format_value(card.get("value"), card.get("format") or "num"),
        "source": card.get("source"),
    } for card in quality.get("cards") or []]
    return Table(
        columns=[
            Column(key="label", label="Signal", align="left", emphasis=True),
            Column(key="value", label="Value", align="right"),
            Column(key="source", label="Source", align="left"),
        ],
        rows=rows,
        caption="Preseason context",
        empty="No preseason context signals are stored.",
    )


def team_stats_table(metrics: dict[str, Any], season: int) -> Table:
    """Seasonal CFBD team statistics, which had no display surface at all."""
    rows = [{"stat_name": _humanize(row["stat_name"]), "stat_value": row["stat_value"]}
            for row in metrics.get("stats") or []]
    return Table(
        columns=[
            Column(key="stat_name", label="Statistic", align="left", emphasis=True),
            Column(key="stat_value", label="Value", format="num"),
        ],
        rows=rows,
        caption=f"{season} team statistics",
        empty=f"No {season} team statistics are stored; CFBD publishes these in season.",
        dense=True,
    )


TEAM_SUMMARY_STATS = (
    # label, team output, opponent output, per-game format,
    # whether higher is favorable in each comparison
    ("Points", "points_for", "points_against", "f1", True, False),
    ("Total yards", "totalYards", "totalYardsOpponent", "f1", True, False),
    ("Rushing yards", "rushingYards", "rushingYardsOpponent", "f1", True, False),
    ("Passing yards", "netPassingYards", "netPassingYardsOpponent", "f1", True, False),
    ("First downs", "firstDowns", "firstDownsOpponent", "f1", True, False),
    ("Giveaways / takeaways", "turnovers", "turnoversOpponent", "f2", False, True),
)


def _team_summary_values(metrics: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    raw = {row["stat_name"]: row.get("stat_value") for row in metrics.get("stats") or []}
    score = metrics.get("score") or {}
    raw.update({"points_for": score.get("points_for"),
                "points_against": score.get("points_against")})
    games = score.get("games") or raw.get("games")
    try:
        games = float(games or 0)
    except (TypeError, ValueError):
        games = 0
    rows = []
    for label, offense_key, defense_key, average_format, offense_higher, defense_higher in TEAM_SUMMARY_STATS:
        def value(key):
            try:
                number = float(raw[key])
            except (KeyError, TypeError, ValueError):
                return None
            return number / games if mode == "per_game" and games else number
        rows.append({"metric": label, "offense": value(offense_key),
                     "defense": value(defense_key),
                     "format": average_format if mode == "per_game" else "big",
                     "offense_higher": offense_higher, "defense_higher": defense_higher})
    # Conversion rates provide more signal than raw attempt totals and should
    # not change when the totals/per-game control changes.
    for label, made, attempts, opp_made, opp_attempts in (
        ("Third down", "thirdDownConversions", "thirdDowns",
         "thirdDownConversionsOpponent", "thirdDownsOpponent"),
        ("Fourth down", "fourthDownConversions", "fourthDowns",
         "fourthDownConversionsOpponent", "fourthDownsOpponent"),
    ):
        def rate(numerator, denominator):
            try:
                return float(raw[numerator]) / float(raw[denominator])
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                return None
        rows.append({"metric": label, "offense": rate(made, attempts),
                     "defense": rate(opp_made, opp_attempts), "format": "rate"})
    return [row for row in rows if row["offense"] is not None or row["defense"] is not None]


def team_summary_table(metrics: dict[str, Any], season: int, mode: str) -> Table:
    rows = _team_summary_values(metrics, mode)
    for row in rows:
        row["offense"] = format_value(row["offense"], row["format"])
        row["defense"] = format_value(row["defense"], row["format"])
    label = "per game" if mode == "per_game" else "totals"
    return Table(
        columns=[Column("metric", "Metric", align="left", emphasis=True),
                 Column("offense", "Team", align="right"),
                 Column("defense", "Opponents", align="right")],
        rows=rows, caption=f"{season} team summary · {label}", dense=True,
        note="Third- and fourth-down rows are rates",
        empty=f"No {season} team totals are stored yet.")


def matchup_summary_table(game: dict[str, Any], away_metrics: dict[str, Any],
                          home_metrics: dict[str, Any], season: int, mode: str) -> Table:
    away = {row["metric"]: row for row in _team_summary_values(away_metrics, mode)}
    home = {row["metric"]: row for row in _team_summary_values(home_metrics, mode)}
    rows = []
    for label, *_ in TEAM_SUMMARY_STATS:
        if label not in away and label not in home:
            continue
        a, h = away.get(label, {}), home.get(label, {})
        row = {"metric": label, "away_offense": a.get("offense"),
               "away_defense": a.get("defense"), "home_offense": h.get("offense"),
               "home_defense": h.get("defense")}
        away_edges = home_edges = 0
        signed_gaps = []
        for suffix, higher in (("offense", a.get("offense_higher", h.get("offense_higher", True))),
                               ("defense", a.get("defense_higher", h.get("defense_higher", False)))):
            av, hv = row[f"away_{suffix}"], row[f"home_{suffix}"]
            if av is None or hv is None or abs(av - hv) < .0001:
                continue
            away_wins = av > hv if higher else av < hv
            winner = "away" if away_wins else "home"
            row[f"{winner}_{suffix}_class"] = "advantage"
            visual = _comparison_visual(av, hv, away_wins)
            row[f"{winner}_{suffix}_sub"] = f"{visual['gap']:.1f}% better"
            signed_gaps.append(-visual["gap"] if away_wins else visual["gap"])
            away_edges += int(away_wins); home_edges += int(not away_wins)
        row["edge"] = (game["away_team"] if away_edges > home_edges else
                       game["home_team"] if home_edges > away_edges else "Even")
        row["edge_class"] = "advantage" if row["edge"] != "Even" else "pending"
        if row["edge"] != "Even" and signed_gaps:
            average_gap = sum(abs(gap) for gap in signed_gaps) / len(signed_gaps)
            home_wins = row["edge"] == game["home_team"]
            visual = _scale_visual(average_gap, not home_wins, full_scale_gap=30.0)
            row["edge_sub"] = f"{average_gap:.1f}% average gap"
            row["edge_scale_position"] = f"{visual['position']:.1f}"
            row["edge_scale_left"] = f"{visual['left']:.1f}"
            row["edge_scale_width"] = f"{visual['width']:.1f}"
            row["edge_scale_side"] = "home" if home_wins else "away"
            row["edge_scale_label"] = (
                f"{game['away_team']} on the left, {game['home_team']} on the right; "
                f"{row['edge']} holds the edge"
            )
        fmt = a.get("format") or h.get("format") or "f1"
        for key in ("away_offense", "away_defense", "home_offense", "home_defense"):
            row[key] = format_value(row[key], fmt)
        rows.append(row)
    return Table(columns=[
        Column("metric", "Metric", align="left", emphasis=True),
        Column("away_offense", game['away_team'], align="right"),
        Column("away_defense", f"{game['away_team']} opp", align="right"),
        Column("home_offense", game['home_team'], align="right"),
        Column("home_defense", f"{game['home_team']} opp", align="right"),
        Column("edge", "Edge", align="left", emphasis=True)], rows=rows, dense=True,
        caption=f"{season} production · {'per game' if mode == 'per_game' else 'totals'}",
        note="A slim tick marks the better value; the edge scale runs away team left to home team right",
        empty=f"No comparable {season} totals are stored.")


def opponent_quality_table(away_team: str, away: dict[str, Any], home_team: str,
                           home: dict[str, Any], season: int) -> Table:
    definitions = (
        ("Opponents faced", "games", "int", None),
        ("Avg opponent pregame Elo", "average_pregame_elo", "f1", True),
        ("Avg opponent latest Elo", "average_latest_elo", "f1", True),
        ("Avg opponent Elo rank", "average_elo_rank", "f1", False),
        ("Elo top-25 opponents", "elo_top_25", "int", True),
        ("Avg opponent CORE", "average_core", "f2", True),
        ("Avg opponent CORE rank", "average_core_rank", "f1", False),
        ("AP-ranked opponents", "poll_ranked", "int", True),
    )
    rows = []
    for label, key, fmt, higher in definitions:
        av, hv = away.get(key), home.get(key)
        if av is None and hv is None:
            continue
        edge = "Even"
        if higher is not None and av is not None and hv is not None and abs(float(av) - float(hv)) > .0001:
            away_wins = float(av) > float(hv) if higher else float(av) < float(hv)
            edge = away_team if away_wins else home_team
        row = {"metric": label, "away": av, "home": hv, "edge": edge,
               "edge_class": "advantage" if edge != "Even" else "pending"}
        if edge == away_team: row["away_class"] = "advantage"
        if edge == home_team: row["home_class"] = "advantage"
        if edge != "Even":
            visual = _comparison_visual(av, hv, away_wins)
            winner_key = "away" if away_wins else "home"
            row[f"{winner_key}_sub"] = f"{visual['gap']:.1f}% better"
            row["edge_sub"] = f"{visual['gap']:.1f}% difference"
            row["edge_scale_position"] = f"{visual['position']:.1f}"
            row["edge_scale_left"] = f"{visual['left']:.1f}"
            row["edge_scale_width"] = f"{visual['width']:.1f}"
            row["edge_scale_side"] = "away" if away_wins else "home"
            row["edge_scale_label"] = (
                f"{away_team} on the left, {home_team} on the right; {edge} holds the edge"
            )
        row["away"] = format_value(av, fmt)
        row["home"] = format_value(hv, fmt)
        rows.append(row)
    return Table(columns=[Column("metric", "Schedule measure", align="left", emphasis=True),
                          Column("away", away_team, align="right"),
                          Column("home", home_team, align="right"),
                          Column("edge", "Tougher slate", align="left", emphasis=True)],
                 rows=rows, caption=f"{season} opponent quality", dense=True,
                 note="Pregame Elo is at kickoff; the centered scale runs away team left to home team right",
                 empty=f"No completed {season} opponents have model coverage.")


def team_opponent_quality_table(team: str, quality: dict[str, Any], season: int, *,
                                upcoming: bool = False) -> Table:
    """Strength of the schedule actually played, for the season on screen.

    Only completed games carry ratings, so on an upcoming schedule this is
    empty by definition rather than missing data, and says so.
    """
    # With no completed games the averages are all absent and only zero counts
    # remain, which reads as "this schedule faced nobody ranked" rather than
    # "nobody has played yet". Say the latter.
    rows: list[dict[str, Any]] = []
    if not quality.get("games"):
        return Table(
            columns=[Column("metric", "Schedule measure", align="left", emphasis=True),
                     Column("value", team, align="right")],
            rows=rows, dense=True, caption=f"{season} opponent quality",
            empty=(f"Opponent quality appears here once {season} games are played."
                   if upcoming
                   else f"No completed {season} opponent ratings are stored."))
    for label, key, fmt in (
        ("Completed opponents", "games", "int"),
        ("Avg opponent pregame Elo", "average_pregame_elo", "f1"),
        ("Avg opponent latest Elo", "average_latest_elo", "f1"),
        ("Avg opponent Elo rank", "average_elo_rank", "f1"),
        ("Elo top-25 opponents", "elo_top_25", "int"),
        ("Avg opponent CORE", "average_core", "f2"),
        ("Avg opponent CORE rank", "average_core_rank", "f1"),
        ("AP-ranked opponents", "poll_ranked", "int"),
    ):
        if quality.get(key) is not None:
            rows.append({"metric": label, "value": format_value(quality[key], fmt)})
    return Table(columns=[Column("metric", "Schedule measure", align="left", emphasis=True),
                          Column("value", team, align="right")], rows=rows, dense=True,
                 caption=f"{season} opponent quality",
                 note="Ratings remain separate because Elo, CORE, and polls use different scales",
                 empty=(f"No {season} games have been played yet." if upcoming
                        else f"No completed {season} opponent ratings are stored."))


def _humanize(name: str) -> str:
    """CFBD stat names are camelCase; headers should not be."""
    spaced = "".join(f" {char}" if char.isupper() else char for char in name).strip()
    return spaced[:1].upper() + spaced[1:]


def roster_table(roster: Sequence[dict[str, Any]], season: int) -> Table:
    """Full roster, sortable by the columns a reader actually scans."""
    rows = [{
        "jersey": player.get("jersey"),
        "name": f"{player.get('first_name')} {player.get('last_name')}",
        "name_url": _player_url(player.get("player_id"), season),
        "position": player.get("position"),
        "class_year": player.get("class_year"),
        "height": height_label(player.get("height")),
        "weight": player.get("weight"),
    } for player in roster]
    return Table(
        columns=[
            Column(key="jersey", label="#", format="rank", align="right"),
            Column(key="name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="class_year", label="Cl", format="rank", align="right"),
            Column(key="height", label="Ht", align="right"),
            Column(key="weight", label="Wt", format="int"),
        ],
        rows=rows,
        caption=f"{season} roster ({len(rows)})",
        empty="No roster is stored for this season.",
        dense=True,
    )


def rankings_table(rankings: dict[str, Any], season: int,
                   brands: dict[int, dict[str, Any]] | None = None) -> Table:
    """The poll as a ranked table instead of a grid of tiles."""
    rows = []
    for row in rankings.get("teams") or []:
        entry = {
            "rank": row.get("rank"),
            "school": row.get("school"),
            "school_url": _team_url(row.get("team_id"), season),
            "conference": row.get("conference") or "Independent",
            "conference_conference": conference_identity(row.get("conference")),
            "first_place_votes": row.get("first_place_votes"),
            "points": row.get("points"),
        }
        brand_cell(entry, "school", (brands or {}).get(row.get("team_id")))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="rank", label="#", format="rank", align="right"),
            Column(key="school", label="Team", align="left", emphasis=True),
            Column(key="conference", label="Conference", align="left"),
            Column(key="first_place_votes", label="1st", format="int",
                   title="First-place votes"),
            Column(key="points", label="Pts", format="big", title="Poll points"),
        ],
        rows=rows,
        caption=f"{rankings.get('poll') or 'Poll'}"
                + (f" · Week {rankings['week']}" if rankings.get("week") else ""),
        empty=f"No poll snapshot is stored for {season}.",
    )


def developments_table(items: Sequence[dict[str, Any]], season: int,
                       brands_by_school: dict[str, dict[str, Any]] | None = None) -> Table:
    """Ranked developments across every ingested platform.

    The ranking column is the point of this table: it replaces "most recently
    posted" with a score built from source expertise, topic importance, recency,
    and how specifically the item resolved to a team, player, or game.
    """
    rows = []
    for item in items:
        headline = (item.get("title") or item.get("body_text") or "").strip()
        teams = item.get("teams") or []
        primary = teams[0]["school"] if teams else None
        entry = {
            "headline": headline[:140] + ("…" if len(headline) > 140 else ""),
            "headline_url": item.get("canonical_url"),
            "headline_sub": " · ".join(item.get("factors") or [])[:150] or None,
            "team": primary,
            "team_url": _team_url(teams[0]["team_id"], season) if teams else None,
            "team_sub": ", ".join(row["school"] for row in teams[1:3]) or None,
            "source": " ".join(filter(None, (
                item.get("source_icon"), item.get("source_type_label"),
                item.get("source_display_name") or item.get("source_entity_name")
                or item.get("publisher_name"),
            ))),
            "source_sub": " · ".join(filter(None, (
                "🔊 Sound" if item.get("makes_sound") else None,
                item.get("published_exact"), item.get("published_relative"),
            ))) or None,
            "role": (item.get("source_role") or "").replace("_", " ").title(),
            "topic": (item.get("topic") or "—").replace("_", " ").title(),
            "score": item.get("score"),
        }
        brand_cell(entry, "team", (brands_by_school or {}).get(primary))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="headline", label="Development", align="left", emphasis=True),
            Column(key="team", label="Team", align="left"),
            Column(key="topic", label="Topic", align="left"),
            Column(key="source", label="Source", align="left"),
            Column(key="role", label="Role", align="left",
                   title="How this source relates to the story"),
            Column(key="score", label="Relevance", format="f1",
                   title="Expertise x topic importance x recency x how specifically it resolved"),
        ],
        rows=rows,
        caption="Ranked developments",
        note="Relevance, not recency",
        empty="Run the ingestion and scoring jobs to populate ranked developments.",
    )


def prospect_table(board: dict[str, Any], season: int, *,
                   include_team: bool = True, dense: bool = False) -> Table:
    """Draft-eligible returners, ranked against a completed draft class."""
    rows = []
    for index, prospect in enumerate(board.get("prospects") or [], start=1):
        entry = {
            "rank": index,
            "player_name": prospect.get("player_name"),
            "player_name_url": _player_url(prospect.get("cfbd_player_id"), season),
            "player_name_sub": prospect.get("headline"),
            "position": prospect.get("position_abbreviation")
                        or position_abbreviation(prospect.get("draft_position")),
            "position_sub": None,
            "team": prospect.get("school"),
            "class_year": prospect.get("class_year"),
            "interest_score": prospect.get("interest_score"),
            "percentile": (prospect.get("percentile") or 0) * 100,
            "percentile_sub": prospect.get("calibration_basis"),
        }
        brand_cell(entry, "team", {"logo": prospect.get("logo"), "logo_dark": prospect.get("logo_dark"),
                               "color": prospect.get("color")})
        rows.append(entry)
    columns = [
        Column(key="rank", label="#", format="rank", align="right"),
        Column(key="player_name", label="Player", align="left", emphasis=True),
        Column(key="position", label="Pos", align="left"),
    ]
    if include_team:
        columns.append(Column(key="team", label="Team", align="left"))
    columns.extend([
        Column(key="class_year", label="Cl", format="rank", align="right",
               title="Class year; draft eligibility is a class-based estimate"),
        Column(key="interest_score", label="PFF", format="f1",
               title="2025 PFF interest score"),
        Column(key="percentile", label="vs drafted", format="pct",
               title="Percentile against players actually drafted at this position"),
    ])
    return Table(
        columns=columns,
        rows=rows,
        caption=f"{board.get('draft_year')} draft watch",
        note=f"calibrated on {board.get('calibration', {}).get('matched_picks', 0)} "
             f"{board.get('calibration', {}).get('draft_year')} picks",
        empty="No draft-eligible returners with a linked PFF profile.",
        dense=dense,
    )


def draft_watch_table(entries: Sequence[dict[str, Any]], season: int, *,
                      dense: bool = False, caption: str = "2027 draft watch") -> Table:
    """The consensus big board, supplemented by our own production profile.

    The board is the spine: its rank and its player are what a reader came for.
    Our percentile is an added column beside it, not a competing ranking, and the
    identity column says plainly whether the row reached a rostered player.
    """
    rows = []
    for entry in entries:
        profile = entry.get("profile_percentile")
        item = {
            "rank": entry.get("rank"),
            "player_name": entry.get("player_name"),
            "player_name_url": _player_url(entry.get("cfbd_player_id"), season),
            "position": position_abbreviation(
                entry.get("position") or entry.get("draft_position")),
            "team": entry.get("team_school") or entry.get("school"),
            "interest_score": entry.get("interest_score"),
            "profile": (profile * 100) if profile is not None else None,
            "verdict": entry.get("verdict"),
        }
        brand_cell(item, "team", {"logo": entry.get("logo"), "logo_dark": entry.get("logo_dark"),
                            "color": entry.get("color")})
        rows.append(item)
    columns = [
        Column(key="rank", label="#", format="rank", align="right",
               title="Rank on the imported consensus board"),
        Column(key="player_name", label="Player", align="left", emphasis=True),
        Column(key="position", label="Pos", align="left",
               title="Draft position group"),
        Column(key="team", label="School", align="left"),
        Column(key="interest_score", label="PFF", format="f1",
               title="2025 PFF interest score, where a profile is linked"),
        Column(key="profile", label="vs drafted", format="pct",
               title="Percentile against players actually drafted at this position"),
        Column(key="verdict", label="Read", align="left",
               title="How the consensus rank and the production profile compare"),
    ]
    return Table(columns=columns, rows=rows, caption=caption,
                 note="consensus board + our profile",
                 empty="No consensus board has been imported for this draft year.",
                 dense=dense)


#: Rows the dashboard draft panel shows before deferring to the full board.
#:
#: The panel is capped at 31rem, which holds about this many dense rows. It was
#: rendering the entire hundred-player consensus board into that space: a
#: scrollbar over ninety hidden rows, and 89 KB — thirty per cent of the whole
#: dashboard — to show twelve.
DRAFT_PANEL_ROWS = 12


def draft_panel_table(entries: Sequence[dict[str, Any]], season: int, *,
                      limit: int = DRAFT_PANEL_ROWS) -> Table:
    """The board in four columns, for the dashboard panel.

    The full table carries position, PFF grade and a verdict. In a sidebar those
    columns squeeze the player name until it wraps mid-word, so the panel keeps
    only rank, who, where, and how the profile compares — and only as many rows
    as the panel can actually show.
    """
    rows = []
    shown = list(entries)[:limit]
    for entry in shown:
        profile = entry.get("profile_percentile")
        item = {
            "rank": entry.get("rank"),
            "player_name": entry.get("player_name"),
            "player_name_url": _player_url(entry.get("cfbd_player_id"), season),
            "player_name_sub": position_abbreviation(
                entry.get("position") or entry.get("draft_position")),
            "team": entry.get("team_school") or entry.get("school"),
            "profile": (profile * 100) if profile is not None else None,
        }
        brand_cell(item, "team", {"logo": entry.get("logo"), "logo_dark": entry.get("logo_dark"),
                            "color": entry.get("color")})
        rows.append(item)
    return Table(
        columns=[
            Column(key="rank", label="#", format="rank", align="right",
                   title="Rank on the imported consensus board"),
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="team", label="School", align="left"),
            Column(key="profile", label="vs drafted", format="pct",
                   title="Percentile against players actually drafted at this position"),
        ],
        rows=rows,
        caption=None,
        note=(f"top {len(rows)} of {len(entries)}" if len(entries) > len(rows) else None),
        empty="No consensus board has been imported.",
    )


def consensus_table(board: list[dict[str, Any]], season: int) -> Table:
    """An imported consensus board, with each row's identity link made visible."""
    rows = []
    for entry in board:
        item = {
            "rank": entry.get("rank"),
            "player_name": entry.get("player_name"),
            "player_name_url": _player_url(entry.get("cfbd_player_id"), season),
            "position": position_abbreviation(entry.get("draft_position")),
            "team": entry.get("team_school") or entry.get("school"),
            "link_status": (entry.get("link_status") or "").replace("_", " ").title(),
            "link_status_sub": entry.get("link_evidence"),
        }
        brand_cell(item, "team", {"logo": entry.get("logo"), "logo_dark": entry.get("logo_dark"),
                            "color": entry.get("color")})
        rows.append(item)
    return Table(
        columns=[
            Column(key="rank", label="#", format="rank", align="right"),
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="team", label="School", align="left"),
            Column(key="link_status", label="Identity", align="left",
                   title="Whether this board row resolved to a roster player, and how"),
        ],
        rows=rows,
        caption="Consensus board",
        empty="No consensus board has been imported for this draft year.",
    )


def divergence_table(entries: list[dict[str, Any]], season: int, *, caption: str,
                     note: str, empty: str, ranked: bool = True) -> Table:
    """Where a consensus board and the production profile disagree."""
    rows = []
    for entry in entries:
        item = {
            "rank": entry.get("rank"),
            "player_name": entry.get("player_name"),
            "player_name_url": _player_url(entry.get("cfbd_player_id"), season),
            "position": position_abbreviation(
                entry.get("position") or entry.get("draft_position")),
            "team": entry.get("school"),
            "profile": (entry.get("profile_percentile") if ranked
                        else entry.get("percentile")) or 0,
            "interest_score": entry.get("interest_score"),
            "note": entry.get("note"),
        }
        item["profile"] = item["profile"] * 100
        brand_cell(item, "team", {"logo": entry.get("logo"), "logo_dark": entry.get("logo_dark"),
                            "color": entry.get("color")})
        rows.append(item)
    columns = []
    if ranked:
        columns.append(Column(key="rank", label="Board #", format="rank", align="right"))
    columns.extend([
        Column(key="player_name", label="Player", align="left", emphasis=True),
        Column(key="position", label="Pos", align="left",
               title="Draft position group"),
        Column(key="team", label="School", align="left"),
        Column(key="interest_score", label="PFF", format="f1", title="2025 PFF interest score"),
        Column(key="profile", label="vs drafted", format="pct",
               title="Percentile against players actually drafted at this position"),
    ])
    return Table(columns=columns, rows=rows, caption=caption, note=note, empty=empty)


def production_groups(production, season):
    """Preseason production split into returning, arrived and departed.

    Each row carries a state class so departed and arrived production are
    visually distinct from returning production, which stays neutral.
    """
    from sports_aggregator.cfb.statlines import category_columns
    groups = []
    for group in production.get("groups") or []:
        rows = []
        for entry in group["players"]:
            row = {
                **entry["stats"],
                "player": entry["player"],
                "player_url": _player_url(entry.get("player_id"), season),
                "player_sub": (f"{entry['state_label']}"
                               + (f" from {entry['earned_at']}" if entry["earned_at"] else "")
                               + (f" → {entry['counterpart']}"
                                  if entry["state"] == "DEPARTED" and entry["counterpart"] else "")),
                "player_class": f"state-{entry['state'].lower()}",
                "position": entry.get("position"),
                "state": entry["state_label"],
                "state_class": f"state-{entry['state'].lower()}",
            }
            rows.append(row)
        counts = group["counts"]
        note = (f"{counts['RETURNING']} returning · {counts['ARRIVED']} arrived "
                f"· {counts['DEPARTED']} departed")
        groups.append({
            "label": group["label"],
            "table": Table(
                columns=[
                    Column(key="player", label="Player", align="left", emphasis=True),
                    Column(key="state", label="Status", align="left"),
                    Column(key="position", label="Pos", align="left"),
                    *category_columns(group["category"]),
                ],
                rows=rows,
                caption=group["label"],
                note=note,
                empty="No production is stored for this category.",
            ),
        })
    return groups


def arrivals_table(arrivals, season, *, caption="Key arrivals"):
    """Key arrivals: transfers with production, signees with a rating.

    Used by the matchup page too, which previously listed portal additions
    alone, so a team's best signee never appeared beside the transfers he is
    competing with.
    """
    rows = []
    for row in arrivals:
        kind = (row.get("movement_type") or "").replace("_", " ").title()
        rows.append({
            "name": row.get("name"),
            "name_url": _player_url(row.get("player_id"), season),
            "name_class": "state-arrived",
            "position": row.get("position"),
            "kind": kind,
            "origin": row.get("origin") or "—",
            "stars": row.get("stars"),
            "rating": row.get("rating"),
            "evidence": row.get("evidence"),
        })
    return Table(
        columns=[
            Column(key="name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="kind", label="Type", align="left"),
            Column(key="origin", label="From", align="left"),
            Column(key="stars", label="★", format="int", title="Recruiting stars"),
            Column(key="rating", label="Rating", format="f3",
                   title="Composite rating: portal rating for transfers, "
                         "recruiting rating for signees"),

        ],
        rows=rows,
        caption=caption,
        note="best of the portal and the signing class, alternating",
        empty="No arrivals identified for this season.",
        dense=True,
    )


def transfer_impact_table(transfers, season, *, caption="Portal impact",
                          departed: bool = False):
    """Portal entries ranked by the evidence that they will matter."""
    rows = [{
        "player_name": row.get("player_name"),
        "player_name_url": _player_url(row.get("player_id"), season),
        "player_name_sub": row.get("impact_label"),
        "player_name_class": "state-departed" if departed else "state-arrived",
        "position": row.get("position"),
        "origin": row.get("origin"),
        "destination": row.get("destination") or "TBD",
        "rating": row.get("rating"),
        "impact_score": row.get("impact_score") if row.get("has_evidence") else None,
    } for row in transfers]
    return Table(
        columns=[
            Column(key="player_name", label="Player", align="left", emphasis=True),
            Column(key="position", label="Pos", align="left"),
            Column(key="origin", label="From", align="left"),
            Column(key="destination", label="To", align="left"),
            Column(key="rating", label="Rating", format="f2",
                   title="CFBD portal rating, which is opinion"),
            Column(key="impact_score", label="Impact", format="f1",
                   title="Prior production, then grade, then rating; blank when no "
                         "prior production is on record"),
        ],
        rows=rows,
        caption=caption,
        note="production first, opinion last",
        empty="No portal entries are stored for this team and season.",
        dense=True,
    )


def model_comparison_table(game, fpi, lines, elo, core=None):
    """Independent model and market views of the same game, side by side.

    FPI, Elo and the betting market are kept as separate rows rather than
    averaged. Where they disagree is the information; a blended number would
    hide exactly the case worth looking at.
    """
    rows = []
    home_fpi = (fpi.get("teams") or {}).get(game["home_team_id"]) or {}
    away_fpi = (fpi.get("teams") or {}).get(game["away_team_id"]) or {}
    if home_fpi or away_fpi:
        margin = home_fpi.get("pred_point_diff")
        rows.append({
            "model": "ESPN FPI",
            "detail": "per-game projection",
            "home_value": _signed(margin),
            "away_value": _signed(away_fpi.get("pred_point_diff")),
            "note": (f"{home_fpi.get('game_projection'):.0f}% / "
                     f"{away_fpi.get('game_projection'):.0f}% win probability"
                     if home_fpi.get("game_projection") is not None
                     and away_fpi.get("game_projection") is not None else None),
        })
    home_elo = (elo or {}).get(game["home_team_id"]) or {}
    away_elo = (elo or {}).get(game["away_team_id"]) or {}
    if home_elo.get("elo") and away_elo.get("elo"):
        rows.append({
            "model": "CFBD Elo",
            "detail": "pregame rating",
            "home_value": str(home_elo["elo"]),
            "away_value": str(away_elo["elo"]),
            "note": f"{home_elo['elo'] - away_elo['elo']:+d} rating gap",
        })
    home_core = (core or {}).get(game["home_team_id"]) or {}
    away_core = (core or {}).get(game["away_team_id"]) or {}
    if home_core or away_core:
        through = max(home_core.get("through_week") or 0,
                      away_core.get("through_week") or 0)
        rows.append({
            "model": "CFBD CORE",
            "detail": f"through week {through}" if through else "current rating",
            "home_value": (f"{home_core['overall']:.1f}"
                           if home_core.get("overall") is not None else None),
            "away_value": (f"{away_core['overall']:.1f}"
                           if away_core.get("overall") is not None else None),
            "note": "overall model rating",
        })
    spread = lines.get("consensus_spread")
    if spread is not None:
        rows.append({
            "model": "Market",
            "detail": f"{lines.get('count', 0)} book(s)",
            "home_value": _signed(-spread),
            "away_value": _signed(spread),
            "note": (f"books differ by {lines['spread_range']:.1f}"
                     if lines.get("spread_range") else "books agree"),
        })
    return Table(
        columns=[
            Column(key="model", label="Model", align="left", emphasis=True),
            Column(key="detail", label="Basis", align="left"),
            Column(key="away_value", label=game["away_team"], align="right"),
            Column(key="home_value", label=game["home_team"], align="right"),
            Column(key="note", label="Read", align="left"),
        ],
        rows=rows,
        caption="Model & market comparison",
        note="kept separate, never averaged",
        empty="No model or market view is stored for this game.",
    )


def _signed(value):
    """Render a margin with an explicit sign, or an em dash when absent."""
    if value is None:
        return None
    return f"{value:+.1f}"


def weather_panel(weather):
    """Kickoff weather reduced to what a preview should say."""
    if not weather.get("available"):
        return None
    latest = weather["latest"]
    return {
        "indoor": weather.get("indoor"),
        "condition": latest.get("condition"),
        "temperature": latest.get("temperature"),
        "wind": latest.get("sustained_wind"),
        "gusts": latest.get("wind_gust"),
        "precipitation_probability": latest.get("precipitation_probability"),
        "flags": weather.get("flags") or [],
        "snapshots": weather.get("snapshots"),
        "movement": weather.get("movement") or {},
        "venue": latest.get("venue"),
        "generated_at": latest.get("forecast_generated_at"),
    }


def market_table(lines, game):
    """Every provider quote for a game, with its movement since opening."""
    rows = [{
        "provider": row.get("provider"),
        "spread": row.get("formatted_spread") or row.get("spread"),
        "spread_move": row.get("spread_move"),
        "over_under": row.get("over_under"),
        "total_move": row.get("total_move"),
        "home_moneyline": row.get("home_moneyline"),
        "away_moneyline": row.get("away_moneyline"),
    } for row in lines.get("providers") or []]
    return Table(
        columns=[
            Column(key="provider", label="Book", align="left", emphasis=True),
            Column(key="spread", label="Spread", align="right"),
            Column(key="spread_move", label="Move", format="signed",
                   title="Change from the opening spread"),
            Column(key="over_under", label="Total", format="f1"),
            Column(key="total_move", label="Move", format="signed",
                   title="Change from the opening total"),
            Column(key="away_moneyline", label=game["away_team"] + " ML", format="signed"),
            Column(key="home_moneyline", label=game["home_team"] + " ML", format="signed"),
        ],
        rows=rows,
        caption="Market",
        note="quotes per book, never averaged into one number",
        empty="No betting lines are stored for this game.",
    )


def games_to_watch_compact(games: Sequence[dict[str, Any]],
                           brands: dict[int, dict[str, Any]] | None = None) -> Table:
    """The slate with both teams named, sized to fit without sideways scrolling.

    An earlier version dropped the home team into a small sub-line to save width.
    That broke the rule the rest of the tables follow -- every entity gets its own
    labeled column at full weight -- so the columns that carry no decision (venue,
    broadcast) are dropped instead, and both teams keep a real column.
    """
    rows = []
    for game in games:
        entry = {
            "week": game.get("week"),
            "week_sub": game.get("date_label"),
            "away_team": game["away_team"],
            "away_team_sub": f"#{game['away_rank']}" if game.get("away_rank") else None,
            "away_team_url": url_for("cfb.game_preview", game_id=game["game_id"]),
            "home_team": game["home_team"],
            "home_team_sub": f"#{game['home_rank']}" if game.get("home_rank") else None,
            "home_team_url": url_for("cfb.game_preview", game_id=game["game_id"]),
            "matchup_edge": game.get("matchup_edge_team") or "Even",
            "matchup_edge_sub": game.get("matchup_edge_unit"),
            "spread": (game.get("market") or {}).get("spread"),
            "attention_score": game.get("attention_score"),
        }
        brand_cell(entry, "away_team", (brands or {}).get(game.get("away_team_id")))
        brand_cell(entry, "home_team", (brands or {}).get(game.get("home_team_id")))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="week", label="Wk", format="rank", align="right"),
            Column(key="away_team", label="Away", align="left", emphasis=True),
            Column(key="home_team", label="Home", align="left", emphasis=True),
            Column(key="matchup_edge", label="Edge", align="left",
                   title="Which team holds the biggest graded unit advantage, and where"),
            Column(key="spread", label="Line", format="f1",
                   title="Consensus spread across books, from the home side"),
            Column(key="attention_score", label="Att", format="int",
                   title="Provisional attention score out of 100"),
        ],
        rows=rows,
        caption=None,
        empty="No upcoming games are stored for this season.",
    )


def games_to_watch_table(games: Sequence[dict[str, Any]],
                        brands: dict[int, dict[str, Any]] | None = None) -> Table:
    """Scored slate with the attention factors kept visible next to the score."""
    rows = []
    for game in games:
        away = f"#{game['away_rank']} {game['away_team']}" if game.get("away_rank") else game["away_team"]
        home = f"#{game['home_rank']} {game['home_team']}" if game.get("home_rank") else game["home_team"]
        entry = {
            "week": game.get("week"),
            "date": game.get("date_label") or game.get("start_label"),
            "date_sub": game.get("time_label"),
            "away_team": away,
            "away_team_url": url_for("cfb.game_preview", game_id=game["game_id"]),
            "home_team": home,
            "home_team_url": url_for("cfb.game_preview", game_id=game["game_id"]),
            "television": game.get("television"),
            "attention_score": game.get("attention_score"),
            "attention_score_sub": ", ".join(game.get("attention_factors") or []) or None,
            "matchup_edge": game.get("matchup_edge"),
            "matchup_edge_sub": game.get("matchup_edge_label"),
        }
        brand_cell(entry, "away_team", (brands or {}).get(game.get("away_team_id")))
        brand_cell(entry, "home_team", (brands or {}).get(game.get("home_team_id")))
        rows.append(entry)
    return Table(
        columns=[
            Column(key="week", label="Wk", format="rank", align="right"),
            Column(key="date", label="Kickoff", align="left"),
            Column(key="away_team", label="Away", align="left", emphasis=True),
            Column(key="home_team", label="Home", align="left", emphasis=True),
            Column(key="television", label="TV", align="left"),
            Column(key="matchup_edge", label="Top matchup", format="f1",
                   title="Interest score of the best graded unit matchup in this game"),
            Column(key="attention_score", label="Attention", format="int",
                   title="Provisional attention score out of 100"),
        ],
        rows=rows,
        caption="Games to watch",
        note="Provisional, explainable scores",
        empty="No upcoming games are stored for this season.",
    )
