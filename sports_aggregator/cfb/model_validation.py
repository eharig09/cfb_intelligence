"""Low-memory validation for in-house play-by-play value models.

The reports are deliberately descriptive about evaluation leakage. If the same
seasons were used to fit a model, those rows are in-sample; use a separately
versioned temporal holdout model for honest out-of-sample performance.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import math
from typing import Any


def _safe_log_loss(probability: float, outcome: float) -> float:
    p = min(.999999, max(.000001, float(probability)))
    return -(outcome * math.log(p) + (1.0 - outcome) * math.log(1.0 - p))


def _wp_accumulator() -> dict[str, float]:
    return {"n": 0.0, "sum_p": 0.0, "sum_y": 0.0, "brier": 0.0, "log_loss": 0.0}


def _wp_add(acc: dict[str, float], p: float, y: float) -> None:
    acc["n"] += 1
    acc["sum_p"] += p
    acc["sum_y"] += y
    acc["brier"] += (p - y) ** 2
    acc["log_loss"] += _safe_log_loss(p, y)


def _wp_finish(acc: dict[str, float]) -> dict[str, Any]:
    n = int(acc["n"])
    if not n:
        return {"plays": 0, "mean_prediction": None, "actual_home_win_rate": None,
                "brier": None, "log_loss": None, "calibration_error": None}
    mean_p = acc["sum_p"] / n
    actual = acc["sum_y"] / n
    return {
        "plays": n,
        "mean_prediction": round(mean_p, 4),
        "actual_home_win_rate": round(actual, 4),
        "brier": round(acc["brier"] / n, 5),
        "log_loss": round(acc["log_loss"] / n, 5),
        "calibration_error": round(abs(mean_p - actual), 5),
    }


def validate_wp(repository, *, from_season: int | None = None,
                to_season: int | None = None, model_version: str = "wp-v1") -> dict[str, Any]:
    """Brier/log-loss/calibration diagnostics without materializing play rows."""
    from sports_aggregator.cfb.win_probability import initialize
    initialize(repository)
    clauses = ["w.model_version=?", "g.completed=1", "g.home_points IS NOT NULL",
               "g.away_points IS NOT NULL", "w.home_win_probability IS NOT NULL"]
    params: list[Any] = [model_version]
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
    sql = f"""
      SELECT p.season,p.period,p.clock_minutes,p.clock_seconds,
             w.home_win_probability,g.home_points,g.away_points
      FROM cfb_play_win_probability w
      JOIN cfb_plays p USING(play_id)
      JOIN games g USING(game_id)
      WHERE {' AND '.join(clauses)}
    """
    overall = _wp_accumulator()
    by_season: dict[int, dict[str, float]] = defaultdict(_wp_accumulator)
    bins: dict[int, dict[str, float]] = defaultdict(_wp_accumulator)
    late = _wp_accumulator()
    with closing(repository._connect()) as connection:
        cursor = connection.execute(sql, params)
        for row in cursor:
            p = float(row["home_win_probability"])
            y = 1.0 if float(row["home_points"]) > float(row["away_points"]) else 0.0
            _wp_add(overall, p, y)
            _wp_add(by_season[int(row["season"])], p, y)
            bin_index = min(9, max(0, int(p * 10)))
            _wp_add(bins[bin_index], p, y)
            try:
                period = int(row["period"]); clock = int(row["clock_minutes"] or 0) * 60 + int(row["clock_seconds"] or 0)
                remaining = max(0, (4 - period) * 900 + clock) if period <= 4 else 0
            except (TypeError, ValueError):
                remaining = None
            if remaining is not None and remaining <= 900:
                _wp_add(late, p, y)
    calibration = []
    weighted_error = 0.0
    total = int(overall["n"])
    for index in range(10):
        packet = _wp_finish(bins[index])
        packet["range"] = f"{index/10:.1f}-{(index+1)/10:.1f}"
        calibration.append(packet)
        if packet["plays"] and packet["calibration_error"] is not None and total:
            weighted_error += packet["plays"] / total * float(packet["calibration_error"])
    return {
        "model_version": model_version,
        "from_season": from_season,
        "to_season": to_season,
        "overall": {**_wp_finish(overall), "expected_calibration_error": round(weighted_error, 5)},
        "late_game_last_15_minutes": _wp_finish(late),
        "by_season": {str(year): _wp_finish(acc) for year, acc in sorted(by_season.items())},
        "calibration_bins": calibration,
        "evaluation_note": (
            "If these seasons overlap the model fitting window, results are in-sample. "
            "Use a separately fitted temporal holdout version for unbiased predictive evaluation."
        ),
    }


def _edp_accumulator() -> dict[str, float]:
    return {"n": 0.0, "sum_pred": 0.0, "sum_actual": 0.0, "abs_error": 0.0, "sq_error": 0.0}


def _edp_add(acc: dict[str, float], predicted: float, actual: float) -> None:
    error = predicted - actual
    acc["n"] += 1
    acc["sum_pred"] += predicted
    acc["sum_actual"] += actual
    acc["abs_error"] += abs(error)
    acc["sq_error"] += error * error


def _edp_finish(acc: dict[str, float]) -> dict[str, Any]:
    n = int(acc["n"])
    if not n:
        return {"plays": 0, "mean_prediction": None, "mean_drive_points": None,
                "mae": None, "rmse": None, "bias": None}
    mean_pred = acc["sum_pred"] / n
    mean_actual = acc["sum_actual"] / n
    return {
        "plays": n,
        "mean_prediction": round(mean_pred, 4),
        "mean_drive_points": round(mean_actual, 4),
        "mae": round(acc["abs_error"] / n, 5),
        "rmse": round(math.sqrt(acc["sq_error"] / n), 5),
        "bias": round(mean_pred - mean_actual, 5),
    }


def validate_edp(repository, *, from_season: int | None = None,
                 to_season: int | None = None, model_version: str = "edp-v1") -> dict[str, Any]:
    """Compare pre-play expected drive points with that possession's final points."""
    from sports_aggregator.cfb.expected_points import initialize
    initialize(repository)
    clauses = ["v.model_version=?", "v.edp_before IS NOT NULL", "p.drive_id IS NOT NULL",
               "m.metric_version='pbp-v1'", "m.rush_pass IN ('rush','pass')"]
    params: list[Any] = [model_version]
    if from_season is not None:
        clauses.append("p.season>=?"); params.append(int(from_season))
    if to_season is not None:
        clauses.append("p.season<=?"); params.append(int(to_season))
    sql = f"""
      SELECT p.season,p.yards_to_goal,v.edp_before,d.points
      FROM cfb_play_value_metrics v
      JOIN cfb_plays p USING(play_id)
      JOIN cfb_play_metrics m ON m.play_id=p.play_id
      JOIN cfb_drive_metrics d ON d.game_id=p.game_id AND d.drive_id=p.drive_id
                              AND d.metric_version=m.metric_version
      WHERE {' AND '.join(clauses)}
    """
    overall = _edp_accumulator()
    by_season: dict[int, dict[str, float]] = defaultdict(_edp_accumulator)
    by_field: dict[str, dict[str, float]] = defaultdict(_edp_accumulator)
    with closing(repository._connect()) as connection:
        for row in connection.execute(sql, params):
            pred = float(row["edp_before"]); actual = float(row["points"] or 0)
            _edp_add(overall, pred, actual)
            _edp_add(by_season[int(row["season"])], pred, actual)
            try:
                ytg = int(row["yards_to_goal"])
            except (TypeError, ValueError):
                label = "unknown"
            else:
                if ytg <= 20: label = "opponent_20_or_closer"
                elif ytg <= 40: label = "opponent_21_40"
                elif ytg <= 60: label = "midfield_band"
                elif ytg <= 80: label = "own_21_40"
                else: label = "own_20_or_deeper"
            _edp_add(by_field[label], pred, actual)
    return {
        "model_version": model_version,
        "from_season": from_season,
        "to_season": to_season,
        "overall": _edp_finish(overall),
        "by_season": {str(year): _edp_finish(acc) for year, acc in sorted(by_season.items())},
        "by_field_position": {label: _edp_finish(acc) for label, acc in sorted(by_field.items())},
        "evaluation_note": (
            "EDP predicts points scored on the current drive, not net possession value. "
            "If these seasons overlap the fitting window, errors are in-sample."
        ),
    }
