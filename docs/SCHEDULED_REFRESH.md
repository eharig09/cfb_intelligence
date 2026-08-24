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

1. Attach a persistent disk at `/var/data`.
2. Set `CFB_DATABASE_PATH=/var/data/cfb.sqlite3`.
3. Set `CFBD_RAW_CACHE_PATH=/var/data/cfbd_raw`.
4. Generate a long random value and set it as `CFB_REFRESH_TOKEN`.

Create/apply the Blueprint from `render.yaml`. For its `cfb-refresh-trigger`
Cron Job, set:

- `CFB_REFRESH_URL=https://YOUR-WEB-SERVICE.onrender.com/internal/cfb-refresh`
- `CFB_REFRESH_TOKEN` to the exact same value used by the web service.

The trigger runs hourly but exits without making a request outside the configured
Eastern refresh hours. On Render, logs, locks, and history live beside the
configured SQLite database on `/var/data`.
