# Scheduled refreshes

The Windows task `Sports News Aggregator - CFB Refresh` runs at 6:00 AM, noon,
6:00 PM, and 11:00 PM in the computer's local time zone. It runs the complete
`bootstrap refresh` dependency chain and starts a missed run when the computer
becomes available again.

The task uses the current calendar year by default. It runs with the current
interactive user so network-backed feeds and APIs remain available. Register or
update it with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_refresh_task.ps1
```

Each run is overlap-safe. `instance/scheduled_refresh.lock` prevents a second
refresh from starting, while locks older than six hours are treated as stale.
Output is written to `instance/refresh_logs/`; compact results are appended to
`instance/scheduled_refresh_history.jsonl`.

National RSS runs early in the refresh, immediately after the canonical CFBD
sync, so it completes before the heavier model and media jobs. Article and local
RSS ingestion also write endpoint, item, and error diagnostics to
`content_ingestion_runs`; the latest results are exposed under `content` at
`/api/v1/cfb/status`. To repair only an empty article stream without running the
full structured-data refresh, run:

```powershell
python -m sports_aggregator.bootstrap refresh --season 2026 --only articles retag cluster score
```

## Reclaiming disk

`python -m sports_aggregator.cfb.prune_cli` reports what the database is
spending space on and reclaims it in tiers. It is a dry run unless a tier is
named, and it prints what each tier costs before touching anything.

Ordered by what you lose, not by what you recover:

| tier | what goes | recoverable by |
| --- | --- | --- |
| `raw` | `content_items.raw_json`, written on every ingest and read by nothing | n/a, nothing is lost |
| `reporting` | articles past a retention window and their links | re-ingesting, if the URLs still resolve |
| `seasons` | per-game box scores and season stats before a cutoff | `bootstrap history` |

The intuition that old news is the weight does not survive measurement: all
6,616 content items hold about 15 MB, while 2.97 million historical box-score
rows hold roughly 128 MB before indexes. On a real database, dropping seasons
before 2021 and vacuuming took 888 MB to 657 MB.

```powershell
python -m sports_aggregator.cfb.prune_cli                       # report only
python -m sports_aggregator.cfb.prune_cli --apply raw --vacuum
python -m sports_aggregator.cfb.prune_cli --before-season 2021 --apply seasons --vacuum
```

Deleting rows leaves free pages inside the file; without `--vacuum` the
database does not shrink on disk. Vacuuming rewrites the file and needs free
space equal to its current size.

## Lock reclamation

The refresh takes a lock in `instance/` and releases it in a `finally`. A
platform kill — which is what an out-of-memory restart is — skips that, so the
lock outlives the process that took it.

The lock records its owner's pid and is checked for liveness before the age
fallback, so a dead holder's lock is reclaimed on the next attempt rather than
blocking every run for the stale window. A running refresh touches the lock
between steps, so the age reflects last progress rather than start time, and
`--stale-lock-hours` defaults to 1 rather than 6.

`GET /internal/cfb-refresh-status` reports `running`, the lock's contents, and
the tail of the newest log. A lock present with no live pid means the previous
run was killed; the next scheduled attempt will take it over.

## Memory

The refresh runs as a subprocess of the web service, so both share the
instance's memory. Each step subprocess is given an address-space ceiling
(`CFB_REFRESH_CHILD_MB`, default 320 MB) so a single step that allocates too
much raises `MemoryError` and is recorded as a failed step, instead of the
platform killing the whole container — which takes the web worker down with it
and strands the lock.

The first line of every refresh log is `fsync`ed before any work begins, so a
run that is killed still leaves a record naming itself.

Useful operations:

```powershell
Get-ScheduledTask -TaskName "Sports News Aggregator - CFB Refresh"
Get-ScheduledTaskInfo -TaskName "Sports News Aggregator - CFB Refresh"
Start-ScheduledTask -TaskName "Sports News Aggregator - CFB Refresh"
```

To use a fixed season or different times, pass `-Season 2026` or, for example,
`-Times 07:00,13:00,19:00` when registering the task again.

## Render

Do not run `sports_aggregator.scheduled_refresh` directly in a separate Render
Cron Job while the app uses SQLite. Render services do not share a filesystem,
so that job would update a private throwaway database instead of the web app's
database.

This repository's `render.yaml` uses a small hourly Cron Job as a trigger. At
6:00 AM, noon, 6:00 PM, and 11:00 PM America/New_York time, it posts to the
web service's authenticated `/internal/cfb-refresh` endpoint. The web service
then starts the lock-safe refresh against its own database and returns immediately.
The hourly check preserves the four local times across daylight-saving changes,
because Render cron expressions themselves use UTC.

In the Render web service:

1. Attach a persistent disk at `/var/data` (5 GB for the current roughly 876 MB
   SQLite database plus WAL growth and provider caches). The repository Blueprint
   now declares this disk for `cfb_intelligence`.
2. Set `CFB_DATABASE_PATH=/var/data/cfb.sqlite3`.
3. Set `CFBD_RAW_CACHE_PATH=/var/data/cfbd_raw`.
4. Set `SPORTSDATAVERSE_CACHE_PATH=/var/data/sportsdataverse` and
   `CFB_WEATHER_CACHE_PATH=/var/data/weather`.
5. Generate a long random value and set it as `CFB_REFRESH_TOKEN`.

Only the first deployment against an empty disk needs `bootstrap initial`. Every
later rebuild remounts the same SQLite database and raw caches; run `bootstrap
refresh` for moving data instead of reseeding. Render does not expose persistent
disks to build or pre-deploy commands, so never put the initial seed in either one.

The Blueprint deliberately runs one Gunicorn worker with four threads and disables
the pandas-heavy legacy Reds/Bengals dashboards on the CFB service. A refresh runs
inside this same instance so it can update SQLite; limiting the resident web process
prevents Gunicorn workers plus an ingestion subprocess from exceeding the service's
memory allocation. If memory alerts continue during a specific bootstrap step,
inspect the refresh log for the last `[ ] step` marker before increasing the instance.
The application also detects Render's built-in `RENDER=true` variable and defaults
legacy dashboards off even when an existing dashboard-managed service has not synced
the Blueprint environment values. `REGISTER_LEGACY_DASHBOARDS=1` remains an explicit
override, but should not be used on the 512 MB instance.

The default `CFB_SQLITE_BUSY_TIMEOUT_MS=60000` lets a refresh writer wait for a
short concurrent transaction. Write transactions reserve their WAL writer slot
before reading, preventing deferred read-to-write upgrades from failing midway
through a refresh.

Create/apply the Blueprint from `render.yaml`. For its `cfb-refresh-trigger`
Cron Job, set:

- `CFB_REFRESH_URL=https://YOUR-WEB-SERVICE.onrender.com/internal/cfb-refresh`
- `CFB_REFRESH_TOKEN` to the exact same value used by the web service.

The trigger runs hourly but exits without making a request outside the configured
Eastern refresh hours. On Render, logs, locks, and history live beside the
configured SQLite database on `/var/data`.
