"""Ingestion commands for secondary structured sources.

Each dataset is isolated: one unavailable source records a failure and the run
continues, because a weather outage should never block an FPI import. Every
command is safe to schedule -- imports are idempotent and cached.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os

from dotenv import load_dotenv

from sports_aggregator.cfb.external import (
    fpi_for_game, import_status, initialize, record_run, store_fpi, store_weather,
    weather_for_game,
)
from sports_aggregator.cfb.repository import CFBRepository
from sports_aggregator.providers.sportsdataverse import SportsDataverseClient, utc_now
from sports_aggregator.providers.weather import OpenMeteoClient, weather_flags, WeatherQuotaExhausted


def _repository(path: str | None = None) -> CFBRepository:
    return CFBRepository(path or os.getenv("CFB_DATABASE_PATH", "instance/cfb.sqlite3"))


def _cache_path(env_name: str, default: str) -> str:
    return (os.getenv(env_name) or default).strip()


def ingest_fpi(repository: CFBRepository, seasons: list[int], *,
               force: bool = False) -> int:
    """Import ESPN FPI projections for one or more seasons."""
    client = SportsDataverseClient(cache_path=_cache_path(
        "SPORTSDATAVERSE_CACHE_PATH", "instance/sportsdataverse"))
    failures = 0
    for season in seasons:
        started = utc_now()
        try:
            asset, rows = client.rows(
                "power_index", season, force=force,
                required_columns=("game_id", "team_id", "teampredptdiff"))
        except Exception as exc:
            failures += 1
            record_run(repository, source="sportsdataverse", dataset="power_index",
                       season=season, started_at=started, status="failed",
                       message=str(exc))
            print(f"power_index {season}: failed ({str(exc)[:120]})")
            continue
        if asset is None:
            record_run(repository, source="sportsdataverse", dataset="power_index",
                       season=season, started_at=started, status="unpublished",
                       message="no release asset for this season")
            print(f"power_index {season}: not published")
            continue
        if rows and "teampredptdiff" not in rows[0]:
            record_run(repository, source="sportsdataverse", dataset="power_index",
                       season=season, started_at=started, status="schema_mismatch",
                       rows_seen=len(rows), asset=asset.name,
                       message=f"columns: {sorted(rows[0])[:6]}")
            print(f"power_index {season}: schema mismatch in {asset.name}")
            failures += 1
            continue
        report = store_fpi(repository, season, rows, asset=asset.name)
        record_run(repository, source="sportsdataverse", dataset="power_index",
                   season=season, started_at=started, status="success",
                   rows_seen=report["seen"], rows_stored=report["stored"],
                   rows_skipped=report["skipped"], asset=asset.name)
        print(f"power_index {season}: stored {report['stored']} "
              f"skipped {report['skipped']} from {asset.name}")
    return failures


def ingest_weather(repository: CFBRepository, season: int, *,
                   limit: int = 60, force: bool = False) -> int:
    """Snapshot kickoff weather for upcoming games inside the forecast horizon."""
    initialize(repository)
    client = OpenMeteoClient(cache_path=_cache_path(
        "CFB_WEATHER_CACHE_PATH", "instance/weather"))
    venues = repository.team_venues()
    started = utc_now()
    generated_at = datetime.now(timezone.utc).isoformat()
    with closing(repository._connect()) as connection:
        games = [dict(row) for row in connection.execute(
            """SELECT game_id,start_date,home_team_id,home_team,away_team,neutral_site
               FROM games WHERE season=? AND completed=0 AND start_date>=?
               ORDER BY start_date LIMIT ?""",
            (season, datetime.now(timezone.utc).isoformat(), limit))]
    stored = skipped = outside = indoor_count = 0
    failures: list[str] = []
    quota_exhausted: str | None = None
    venue_payloads: dict[tuple[float, float], dict] = {}
    venue_errors: dict[tuple[float, float], str] = {}
    for game in games:
        if not OpenMeteoClient.within_horizon(game["start_date"]):
            outside += 1
            continue
        venue = venues.get(game["home_team_id"])
        if not venue or venue.get("latitude") is None or venue.get("longitude") is None:
            skipped += 1
            continue
        indoor = bool(venue.get("dome"))
        key = (round(float(venue["latitude"]), 5), round(float(venue["longitude"]), 5))
        if key in venue_errors:
            failures.append(f"{game['game_id']}: {venue_errors[key]}")
            continue
        payload = venue_payloads.get(key)
        if payload is None:
            try:
                payload = client.venue_forecast(venue["latitude"], venue["longitude"],
                                                force=force)
                venue_payloads[key] = payload
            except WeatherQuotaExhausted as exc:
                # Nothing will succeed until the allowance resets, and each
                # venue costs several seconds of retry backoff on the way to
                # the same answer. Stop rather than confirm it 56 times.
                quota_exhausted = str(exc)[:220]
                failures.append(f"{game['game_id']}: {quota_exhausted}")
                break
            except Exception as exc:
                message = str(exc)[:220]
                venue_errors[key] = message
                failures.append(f"{game['game_id']}: {message}")
                continue
        forecast = OpenMeteoClient.at_kickoff(payload, game["start_date"])
        if forecast is None:
            outside += 1
            continue
        flags = [] if indoor else weather_flags(forecast)
        store_weather(repository, game["game_id"], forecast, flags=flags,
                      venue=venue.get("venue_name") or "", latitude=venue["latitude"],
                      longitude=venue["longitude"], indoor=indoor,
                      generated_at=generated_at)
        stored += 1
        indoor_count += int(indoor)
    status = "success" if not failures else ("partial" if stored else "failed")
    record_run(repository, source="open-meteo", dataset="game_weather", season=season,
               started_at=started, status=status, rows_seen=len(games),
               rows_stored=stored, rows_skipped=skipped + outside,
               message="; ".join(failures[:3]))
    print(f"game_weather {season}: stored {stored} ({indoor_count} indoor), "
          f"outside horizon {outside}, no venue {skipped}, failures {len(failures)}")
    if quota_exhausted:
        # Say it stopped on purpose, so the log does not read as a silent gap.
        print(f"weather stopped early: {quota_exhausted}. "
              "Remaining venues were not requested; the allowance resets daily.")
    if failures:
        print("weather failure samples:")
        for failure in failures[:3]:
            print(f"  - {failure}")
    if venue_errors:
        print(f"weather venue requests: {len(venue_payloads)} succeeded, "
              f"{len(venue_errors)} failed (deduplicated across {len(games)} games)")
    return len(failures)


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ingest secondary structured sources")
    parser.add_argument("command", choices=("fpi", "weather", "status", "sources"))
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--from-year", type=int, default=None)
    parser.add_argument("--to-year", type=int, default=None)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--force", action="store_true", help="Bypass local caches")
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)
    repository = _repository(args.database)

    if args.command == "sources":
        # What each upstream dataset currently publishes, before importing it.
        client = SportsDataverseClient(cache_path=_cache_path(
            "SPORTSDATAVERSE_CACHE_PATH", "instance/sportsdataverse"))
        for entry in client.status():
            seasons = entry.get("seasons") or []
            span = f"{seasons[0]}-{seasons[-1]}" if seasons else "none"
            print(f"  {entry['dataset']:20s} assets={entry.get('assets', 0):>4} "
                  f"seasons={span:12s} available={entry.get('available')}")
        return 0

    if args.command == "status":
        print(json.dumps(import_status(repository), indent=2, default=str))
        return 0

    if args.command == "fpi":
        first = args.from_year or args.season
        last = args.to_year or args.season
        return 1 if ingest_fpi(repository, list(range(first, last + 1)),
                               force=args.force) else 0

    return 1 if ingest_weather(repository, args.season, limit=args.limit,
                               force=args.force) else 0


if __name__ == "__main__":
    raise SystemExit(main())
