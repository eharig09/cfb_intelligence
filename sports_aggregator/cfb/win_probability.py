"""Empirical in-house win probability and leverage model.

WP-v1 is intentionally dependency-free and auditable. Historical play states
are bucketed by period/time, home score margin, possession and field position;
the target is the actual home-team game result. Sparse cells shrink toward the
overall home win rate. This is a first model that can later be replaced by a
calibrated logistic/GBM model without changing stored raw plays.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

MODEL_VERSION = "wp-v1"
MIN_CELL = 30


def initialize(repository) -> None:
    from sports_aggregator.cfb.play_by_play import initialize as initialize_pbp
    initialize_pbp(repository)
    with closing(repository._connect()) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cfb_win_probability_model (
          model_version TEXT NOT NULL,
          period_bucket INTEGER NOT NULL,
          time_bucket INTEGER NOT NULL,
          margin_bucket INTEGER NOT NULL,
          possession_bucket INTEGER NOT NULL,
          field_bucket INTEGER NOT NULL,
          samples INTEGER NOT NULL,
          home_win_probability REAL NOT NULL,
          fitted_at TEXT NOT NULL,
          PRIMARY KEY(model_version,period_bucket,time_bucket,margin_bucket,possession_bucket,field_bucket)
        );
        CREATE TABLE IF NOT EXISTS cfb_play_win_probability (
          play_id TEXT NOT NULL,
          model_version TEXT NOT NULL,
          home_win_probability REAL,
          leverage REAL,
          scored_at TEXT NOT NULL,
          PRIMARY KEY(play_id,model_version),
          FOREIGN KEY(play_id) REFERENCES cfb_plays(play_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cfb_play_wp_model
          ON cfb_play_win_probability(model_version,play_id);
        """)
        connection.commit()


def _period(value: Any) -> int:
    try: p = int(value)
    except (TypeError, ValueError): return 0
    return min(max(p,1),5)


def _time_bucket(period: Any, minutes: Any, seconds: Any) -> int:
    try:
        p = int(period); remain = int(minutes) * 60 + int(seconds)
    except (TypeError, ValueError):
        return 0
    if p <= 4:
        game_remain = max(0, (4-p)*900 + remain)
    else:
        game_remain = 0
    # Five-minute game-time buckets, 0 = final five minutes.
    return min(12, game_remain // 300)


def _margin_bucket(margin: int) -> int:
    # Roughly possession-sized buckets with capped blowout tails.
    if margin <= -29: return -5
    if margin <= -15: return -4
    if margin <= -9: return -3
    if margin <= -1: return -1 if margin >= -8 else -2
    if margin == 0: return 0
    if margin <= 8: return 1
    if margin <= 14: return 2
    if margin <= 28: return 3 if margin <= 21 else 4
    return 5


def _field_bucket(yards_to_goal: Any, offense_is_home: bool) -> int:
    try: ytg = max(1,min(100,int(yards_to_goal)))
    except (TypeError, ValueError): return 0
    # Convert to home-team distance from scoring goal so the field feature has
    # the same orientation whether home is possessing or defending.
    home_ytg = ytg if offense_is_home else 100-ytg
    if home_ytg <= 20: return 1
    if home_ytg <= 40: return 2
    if home_ytg <= 60: return 3
    if home_ytg <= 80: return 4
    return 5


def _scores(row: dict[str, Any]) -> tuple[int,int,bool]:
    offense_is_home = str(row.get("offense")) == str(row.get("home_team"))
    offense_score = int(row.get("offense_score") or 0)
    defense_score = int(row.get("defense_score") or 0)
    return ((offense_score, defense_score, True) if offense_is_home
            else (defense_score, offense_score, False))


def state_key(row: dict[str, Any]) -> tuple[int,int,int,int,int]:
    home_score, away_score, home_possession = _scores(row)
    return (
        _period(row.get("period")),
        _time_bucket(row.get("period"),row.get("clock_minutes"),row.get("clock_seconds")),
        _margin_bucket(home_score-away_score),
        1 if home_possession else 0,
        _field_bucket(row.get("yards_to_goal"),home_possession),
    )


def fit_model(repository, *, from_season: int | None = None,
              to_season: int | None = None, model_version: str = MODEL_VERSION,
              min_cell: int = MIN_CELL) -> dict[str, Any]:
    initialize(repository)
    clauses = ["g.completed=1", "g.home_points IS NOT NULL", "g.away_points IS NOT NULL"]
    params: list[Any] = []
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
    with closing(repository._connect()) as connection:
        rows = [dict(row) for row in connection.execute(f"""
          SELECT p.*,g.home_points,g.away_points
          FROM cfb_plays p JOIN games g USING(game_id)
          WHERE {' AND '.join(clauses)}
        """, params).fetchall()]
    cells: dict[tuple[int,int,int,int,int], list[float]] = defaultdict(list)
    outcomes = []
    for row in rows:
        key = state_key(row)
        if 0 in (key[0], key[4]):
            continue
        outcome = 1.0 if float(row["home_points"]) > float(row["away_points"]) else 0.0
        cells[key].append(outcome); outcomes.append(outcome)
    base = sum(outcomes)/len(outcomes) if outcomes else .5
    now = datetime.now(timezone.utc).isoformat(); output = []
    for key, values in cells.items():
        n = len(values); raw = sum(values)/n
        weight = n/(n+max(1,int(min_cell)))
        probability = weight*raw + (1-weight)*base
        output.append((*key,n,probability))
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM cfb_win_probability_model WHERE model_version=?",(model_version,))
        connection.executemany("""INSERT INTO cfb_win_probability_model
          (model_version,period_bucket,time_bucket,margin_bucket,possession_bucket,field_bucket,
           samples,home_win_probability,fitted_at) VALUES(?,?,?,?,?,?,?,?,?)""",
          [(model_version,*row,now) for row in output])
        connection.commit()
    return {"model_version":model_version,"plays":len(rows),"cells":len(output),
            "base_home_win_rate":round(base,4),"from_season":from_season,"to_season":to_season}


def _lookup(table: dict[tuple[int,int,int,int,int],tuple[float,int]], key: tuple[int,int,int,int,int]) -> float | None:
    if key in table: return table[key][0]
    period,time,margin,possession,field = key
    # Back off field first, then possession. Time + margin stay fixed because
    # they dominate win probability late in games.
    candidates=[(v,n) for (p,t,m,_pos,_f),(v,n) in table.items() if p==period and t==time and m==margin]
    if candidates:
        total=sum(n for _,n in candidates); return sum(v*n for v,n in candidates)/total
    candidates=[(v,n) for (p,_t,m,_pos,_f),(v,n) in table.items() if p==period and m==margin]
    if candidates:
        total=sum(n for _,n in candidates); return sum(v*n for v,n in candidates)/total
    return None


def score_plays(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = MODEL_VERSION) -> dict[str,Any]:
    initialize(repository)
    with closing(repository._connect()) as connection:
        model_rows=connection.execute("""SELECT period_bucket,time_bucket,margin_bucket,
          possession_bucket,field_bucket,home_win_probability,samples
          FROM cfb_win_probability_model WHERE model_version=?""",(model_version,)).fetchall()
        table={(int(r[0]),int(r[1]),int(r[2]),int(r[3]),int(r[4])):(float(r[5]),int(r[6])) for r in model_rows}
        clauses=[]; params=[]
        if from_season is not None: clauses.append("season>=?"); params.append(int(from_season))
        if to_season is not None: clauses.append("season<=?"); params.append(int(to_season))
        where=("WHERE "+" AND ".join(clauses)) if clauses else ""
        plays=[dict(row) for row in connection.execute(f"""SELECT * FROM cfb_plays {where}
          ORDER BY game_id,period,clock_minutes DESC,clock_seconds DESC,drive_number,play_number""",params).fetchall()]
    if not table: return {"model_version":model_version,"scored":0,"reason":"model_not_fitted"}
    now=datetime.now(timezone.utc).isoformat(); output=[]
    by_game: dict[int,list[dict[str,Any]]] = defaultdict(list)
    for row in plays:
        row["_wp"]=_lookup(table,state_key(row))
        by_game[int(row["game_id"])].append(row)
    for game_rows in by_game.values():
        for index,row in enumerate(game_rows):
            probability=row.get("_wp")
            next_probability = game_rows[index+1].get("_wp") if index+1 < len(game_rows) else None
            # The jump from this pre-play state to the next pre-play state was
            # caused by this play, so leverage belongs on this play's text.
            leverage=(abs(float(next_probability)-float(probability))
                      if probability is not None and next_probability is not None else None)
            output.append((row["play_id"],model_version,probability,leverage,now))
    with closing(repository._connect()) as connection:
        if output:
            ids=[r[0] for r in output]; placeholders=",".join("?" for _ in ids)
            connection.execute(f"DELETE FROM cfb_play_win_probability WHERE model_version=? AND play_id IN ({placeholders})",
                               (model_version,*ids))
            connection.executemany("INSERT INTO cfb_play_win_probability VALUES(?,?,?,?,?)",output)
        connection.commit()
    return {"model_version":model_version,"scored":len(output)}


def game_turning_points(repository, game_id: int, *, model_version: str = MODEL_VERSION,
                        limit: int = 6) -> list[dict[str,Any]]:
    initialize(repository)
    with closing(repository._connect()) as connection:
        rows=[dict(row) for row in connection.execute("""SELECT p.play_id,p.period,p.clock_minutes,
          p.clock_seconds,p.offense,p.defense,p.play_type,p.play_text,p.offense_score,p.defense_score,
          w.home_win_probability,w.leverage FROM cfb_plays p JOIN cfb_play_win_probability w USING(play_id)
          WHERE p.game_id=? AND w.model_version=? AND w.leverage IS NOT NULL
          ORDER BY w.leverage DESC LIMIT ?""",(int(game_id),model_version,int(limit))).fetchall()]
    return rows
