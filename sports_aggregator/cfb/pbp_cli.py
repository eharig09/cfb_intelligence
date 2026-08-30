"""CLI for play-by-play backfill and in-house model development."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
import sqlite3
import time

from dotenv import load_dotenv

from sports_aggregator.cfb.cfbd import CFBDClient, CFBDConfigurationError, FINISHED_WEEK_TTL
from sports_aggregator.cfb.epa_validation import validate_epa
from sports_aggregator.cfb.expected_points import fit_model as fit_edp, score_plays as score_edp
from sports_aggregator.cfb.expected_points_v2 import fit_model as fit_ep
from sports_aggregator.cfb.expected_points_v2 import score_plays as score_epa
from sports_aggregator.cfb.expected_points_v2 import validate_model as validate_ep
from sports_aggregator.cfb.model_validation import validate_edp, validate_wp
from sports_aggregator.cfb.pace import game_pace_summary
from sports_aggregator.cfb.play_by_play import replace_week_plays, derive_week
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.cfb.win_probability import fit_model as fit_wp, score_plays as score_wp
from sports_aggregator.cfb.win_probability_v2 import fit_model as fit_wp_v2, score_plays as score_wp_v2
from sports_aggregator.cfb.wp_calibration import fit_calibration as fit_wp_calibration
from sports_aggregator.cfb.wp_calibration import score_calibrated as score_wp_calibrated


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CFB play-by-play analytics")
    p.add_argument("command", choices=(
        "backfill", "derive",
        "fit-edp", "score-edp", "validate-edp",
        "fit-ep", "score-epa", "validate-ep", "validate-epa",
        "fit-wp", "score-wp", "fit-wp-v2", "score-wp-v2", "validate-wp",
        "fit-wp-calibration", "score-wp-calibrated", "rebuild-values", "pace"))
    p.add_argument("--year", type=int, default=datetime.now().year)
    p.add_argument("--from-year", type=int, default=None)
    p.add_argument("--to-year", type=int, default=None)
    p.add_argument("--week", type=int, default=None)
    p.add_argument("--game-id", type=int, default=None)
    p.add_argument("--model-version", default=None,
                   help="Optional output/model version. Defaults by model family.")
    p.add_argument("--source-model-version", default=None,
                   help="Source WP version for calibration commands.")
    p.add_argument("--epochs", type=int, default=8,
                   help="Newton/IRLS iterations for wp-v2 logistic model.")
    p.add_argument("--learning-rate", type=float, default=1.0,
                   help="Damping multiplier for wp-v2 Newton steps (0-1).")
    p.add_argument("--l2", type=float, default=0.0005,
                   help="L2 regularization for wp-v2.")
    p.add_argument("--force", action="store_true")
    p.add_argument("--database", default=None)
    return p


def _years(args) -> tuple[int, int]:
    first = args.from_year or args.year
    last = args.to_year or args.year
    if first > last:
        raise ValueError("--from-year must not be after --to-year")
    return first, last


def _model_version(args, family: str) -> str:
    defaults = {"edp": "edp-v1", "ep": "ep-v1", "wp": "wp-v1", "wp-v2": "wp-v2"}
    return str(args.model_version or defaults[family])


def _week_ready(repository: CFBRepository, year: int, week: int) -> tuple[bool, int]:
    try:
        with closing(repository._connect()) as connection:
            raw = int(connection.execute(
                "SELECT COUNT(*) FROM cfb_plays WHERE season=? AND week=?", (int(year), int(week))
            ).fetchone()[0])
            if raw <= 0:
                return False, 0
            derived = int(connection.execute("""
                SELECT COUNT(*) FROM cfb_play_metrics m
                JOIN cfb_plays p ON p.play_id=m.play_id
                WHERE p.season=? AND p.week=? AND m.metric_version='pbp-v1'
            """, (int(year), int(week))).fetchone()[0])
        return derived == raw, raw
    except sqlite3.Error:
        return False, 0


def _replace_with_lock_retry(repository: CFBRepository, raw, *, year: int, week: int) -> int:
    delays = (15, 30, 60)
    for attempt in range(len(delays) + 1):
        try:
            return replace_week_plays(repository, raw, season=year, week=week)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).casefold() or attempt >= len(delays):
                raise
            delay = delays[attempt]
            print(f"{year} week {week}: database locked; retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError("unreachable")


def main(argv: list[str] | None = None, *, client=None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    repository = CFBRepository(args.database or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))
    first, last = _years(args)

    if args.command == "pace":
        if not args.game_id:
            raise SystemExit("pace requires --game-id")
        print(json.dumps(game_pace_summary(repository, args.game_id), indent=2, default=str))
        return 0

    if args.command == "derive":
        total = {"plays": 0, "drives": 0}
        for year in range(first, last + 1):
            weeks = [args.week] if args.week is not None else repository.completed_weeks(year)
            for week in weeks:
                result = derive_week(repository, season=year, week=int(week))
                print(f"{year} week {week}: plays={result['plays']} drives={result['drives']}")
                total["plays"] += result["plays"]
                total["drives"] += result["drives"]
        print(json.dumps(total))
        return 0

    if args.command == "fit-edp":
        version = _model_version(args, "edp")
        print(json.dumps(fit_edp(repository, from_season=first, to_season=last,
                                 model_version=version), indent=2))
        return 0
    if args.command == "score-edp":
        version = _model_version(args, "edp")
        print(json.dumps(score_edp(repository, from_season=first, to_season=last,
                                   model_version=version), indent=2))
        return 0
    if args.command == "validate-edp":
        version = _model_version(args, "edp")
        print(json.dumps(validate_edp(repository, from_season=first, to_season=last,
                                     model_version=version), indent=2))
        return 0

    if args.command == "fit-ep":
        version = _model_version(args, "ep")
        print(json.dumps(fit_ep(repository, from_season=first, to_season=last,
                                model_version=version), indent=2))
        return 0
    if args.command == "score-epa":
        version = _model_version(args, "ep")
        print(json.dumps(score_epa(repository, from_season=first, to_season=last,
                                   model_version=version), indent=2))
        return 0
    if args.command == "validate-ep":
        version = _model_version(args, "ep")
        print(json.dumps(validate_ep(repository, from_season=first, to_season=last,
                                    model_version=version), indent=2))
        return 0
    if args.command == "validate-epa":
        version = _model_version(args, "ep")
        print(json.dumps(validate_epa(repository, from_season=first, to_season=last,
                                     model_version=version), indent=2))
        return 0

    if args.command == "fit-wp":
        version = _model_version(args, "wp")
        print(json.dumps(fit_wp(repository, from_season=first, to_season=last,
                                model_version=version), indent=2))
        return 0
    if args.command == "score-wp":
        version = _model_version(args, "wp")
        print(json.dumps(score_wp(repository, from_season=first, to_season=last,
                                  model_version=version), indent=2))
        return 0
    if args.command == "fit-wp-v2":
        version = _model_version(args, "wp-v2")
        print(json.dumps(fit_wp_v2(
            repository, from_season=first, to_season=last, model_version=version,
            epochs=args.epochs, learning_rate=args.learning_rate, l2=args.l2), indent=2))
        return 0
    if args.command == "score-wp-v2":
        version = _model_version(args, "wp-v2")
        print(json.dumps(score_wp_v2(repository, from_season=first, to_season=last,
                                     model_version=version), indent=2))
        return 0
    if args.command == "validate-wp":
        version = _model_version(args, "wp")
        print(json.dumps(validate_wp(repository, from_season=first, to_season=last,
                                    model_version=version), indent=2))
        return 0

    if args.command == "fit-wp-calibration":
        source = str(args.source_model_version or "wp-v1")
        version = str(args.model_version or f"{source}-calibrated")
        print(json.dumps(fit_wp_calibration(
            repository, source_model_version=source, calibration_version=version,
            from_season=first, to_season=last), indent=2))
        return 0
    if args.command == "score-wp-calibrated":
        source = str(args.source_model_version or "wp-v1")
        version = str(args.model_version or f"{source}-calibrated")
        print(json.dumps(score_wp_calibrated(
            repository, source_model_version=source, calibration_version=version,
            output_model_version=version, from_season=first, to_season=last), indent=2))
        return 0

    if args.command == "rebuild-values":
        edp = score_edp(repository, from_season=first, to_season=last,
                        model_version=_model_version(args, "edp"))
        ep = score_epa(repository, from_season=first, to_season=last,
                       model_version=_model_version(args, "ep"))
        wp = score_wp(repository, from_season=first, to_season=last,
                      model_version=_model_version(args, "wp"))
        print(json.dumps({"edp": edp, "ep": ep, "wp": wp}, indent=2))
        return 0

    client = client or CFBDClient(raw_cache_path=os.getenv("CFBD_RAW_CACHE_PATH", "instance/cfbd_raw"))
    if not client.configured:
        raise CFBDConfigurationError("CFBD_API_KEY is required for PBP backfill")
    failures = []
    total = 0
    cached_total = 0
    for year in range(first, last + 1):
        weeks = [args.week] if args.week is not None else repository.completed_weeks(year)
        if not weeks:
            print(f"{year}: no completed weeks stored; sync game history first")
            continue
        for week in weeks:
            week = int(week)
            ready, count = _week_ready(repository, year, week)
            if ready and not args.force:
                cached_total += count
                print(f"{year} week {week}: cached ({count} plays)")
                continue
            try:
                raw = client.get("/plays", {
                    "year": year, "week": week, "seasonType": "both", "classification": "fbs"
                }, cache_ttl_seconds=FINISHED_WEEK_TTL, force=args.force)
                count = _replace_with_lock_retry(repository, raw, year=year, week=week)
                total += count
                print(f"{year} week {week}: {count} plays")
            except Exception as exc:
                failures.append(f"{year} week {week}")
                print(f"{year} week {week}: failed ({exc})")
    print(f"PBP backfill complete: {total} new/rebuilt plays, {cached_total} cached plays, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())