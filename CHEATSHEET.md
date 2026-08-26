# Operations cheat sheet

Commands for running, inspecting and repairing this project, in the three
places you actually type them: **Windows PowerShell** locally, **bash** on a
Render shell, and **git** anywhere.

The single most common mistake is running PowerShell syntax in a bash shell or
the reverse. They are not interchangeable — see [Syntax differences](#syntax-differences)
at the bottom.

---

## Which shell am I in?

| prompt looks like | you are in | use |
| --- | --- | --- |
| `PS C:\Users\...>` | PowerShell on Windows | PowerShell syntax |
| `render@srv-...:~/project/src$` | bash on the **web service** | bash syntax, `/var/data` exists |
| `render@cfb-refresh-trigger-...:~$` | bash on the **cron job** | bash syntax, **no** `/var/data` |

The disk mounts only on the web service (`cfb_intelligence`). On the cron
shell, every `/var/data` path fails with "No such file or directory" — that is
the wrong service, not a missing file.

---

## Local development (PowerShell)

```powershell
# Run the app (http://127.0.0.1:5000)
python run.py

# Tests
python -m pytest -q                          # everything
python -m pytest tests/test_page_cache.py -q # one file
python -m pytest -q -k "continuity"          # by name

# Point at a specific database
$env:CFB_DATABASE_PATH = "instance/cfb.sqlite3"

# Disable page caching while working on templates
$env:CFB_PAGE_CACHE_SECONDS = "0"
```

### Port 5000 already in use

Multiple Flask servers can bind the same port on Windows, and requests go to
whichever wins the race — so you can be served stale code no matter how many
times you reload.

```powershell
netstat -ano | Select-String ":5000 " | Select-String "LISTENING"
Get-Process -Id <PID> | Select-Object Id, ProcessName, StartTime, Path
Stop-Process -Id <PID1>,<PID2> -Force
```

Always confirm what a PID is before killing it — PIDs are reused.

---

## Data pipeline

Same commands in both shells; only the surrounding syntax differs.

```bash
# Plan and status, neither of which changes anything
python -m sports_aggregator.bootstrap plan   --season 2026
python -m sports_aggregator.bootstrap status --season 2026

# Full build (first run on an empty database) and routine update
python -m sports_aggregator.bootstrap initial --season 2026
python -m sports_aggregator.bootstrap refresh --season 2026

# Only certain steps
python -m sports_aggregator.bootstrap refresh --season 2026 --only articles retag cluster roles score

# Historical backfill, one season per process
python -m sports_aggregator.bootstrap history --season 2026 --from-year 2019 --to-year 2025
```

### Individual steps

```bash
python -m sports_aggregator.cfb.cli sync --year 2026            # CFBD core sync
python -m sports_aggregator.cfb.external_cli weather --season 2026
python -m sports_aggregator.social.content_cli ingest-reporting --season 2026
python -m sports_aggregator.social.content_cli ingest-local-reporting --season 2026
python -m sports_aggregator.social.content_cli retag   --season 2026
python -m sports_aggregator.social.content_cli cluster                  # story clustering
python -m sports_aggregator.social.content_cli roles                    # source role + evidence
python -m sports_aggregator.social.content_cli score                    # relevance
python -m sports_aggregator.social.content_cli status
```

Order matters for the last four: `retag` → `cluster` → `roles` → `score`.
Role determination reads an item's position in its cluster, and relevance
weights the role.

---

## Render: inspecting a refresh

Shell into **`cfb_intelligence`** (the web service — the cron job has no disk).

```bash
ls -la /var/data/                                        # sanity check you are on the right service
cat /var/data/scheduled_refresh.lock 2>/dev/null || echo "no lock held"
tail -2 /var/data/scheduled_refresh_history.jsonl        # last completed runs
ls -lt /var/data/refresh_logs/ | head -5
tail -40 "$(ls -t /var/data/refresh_logs/refresh-*.log | head -1)"
```

### Reading the result

```json
{"status":"degraded","seconds":465.8,"degraded_count":2,
 "required_failure_count":0,"parent_peak_rss_mb":33.7,"child_peak_rss_mb":272.9}
```

| field | meaning |
| --- | --- |
| `required_failure_count` | non-zero is a real problem; optional steps do not count |
| `degraded_count` | optional steps that failed — a step fails only when it stored nothing or lost >25% of endpoints |
| `child_peak_rss_mb` | heaviest single step; the ceiling is `CFB_REFRESH_CHILD_MB` (320) |
| `log` | the file to `tail` for detail |

A `status` of `degraded` with `required_failure_count: 0` means the refresh
worked and some optional sources were flaky.

### Running one manually

```bash
python -m sports_aggregator.scheduled_refresh --season 2026 --profile light
python -m sports_aggregator.scheduled_refresh --season 2026 --profile heavy

# Survives the shell closing
nohup python -m sports_aggregator.scheduled_refresh --season 2026 --profile light \
  > /tmp/manual-refresh.log 2>&1 &
tail -f /tmp/manual-refresh.log
```

`light` runs the moving sources only. `heavy` runs everything and takes
considerably longer.

### A stuck lock

A refresh killed by the platform cannot release its lock. The next attempt
reclaims it automatically once the holding process is gone, so this is rarely
needed:

```bash
cat /var/data/scheduled_refresh.lock      # note the pid
kill -0 <pid> 2>/dev/null && echo "alive" || echo "dead — will be reclaimed"
rm /var/data/scheduled_refresh.lock       # last resort, only if nothing is running
```

---

## Refresh status over HTTP

Needs `CFB_REFRESH_TOKEN` from the Render dashboard (`cfb_intelligence` →
Environment). Reading the files on the web-service shell needs no token and is
usually easier.

> **`curl` in PowerShell is not curl.** It is an alias for `Invoke-WebRequest`,
> so `-H` binds to `-Headers`, which wants a hashtable, and you get
> *"Cannot convert the ... value of type System.String to type
> System.Collections.IDictionary"*. Write `curl.exe` to get the real one, or
> use the native form below.

**PowerShell.** `Read-Host` keeps the token out of your shell history: the
command is recorded, what you type into it is not.

```powershell
$token = Read-Host "Token"
Invoke-RestMethod -Uri "https://cfb-intelligence.onrender.com/internal/cfb-refresh-status" `
  -Headers @{ Authorization = "Bearer $token" } |
  ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 refresh-status.json
```

**bash:**

```bash
TOKEN='your-token'
curl -s -H "Authorization: Bearer $TOKEN" \
  https://cfb-intelligence.onrender.com/internal/cfb-refresh-status \
  | python3 -m json.tool | head -60
```

### Triggering a refresh remotely

`profile=auto` is what the cron sends: the web service reads the schedule and
decides. Name a profile to override it.

| Profile | What it runs | Measured |
| --- | --- | --- |
| `scores` | games dataset + betting lines | 5s |
| `light` | 8 steps: the full CFBD sync plus the article and social wire | not yet timed |
| `heavy` | all 22 steps, including the per-team roster crawl | 425s clean, 848s degraded |

`auto` picks `scores` whenever a game has kicked off in the last six hours and
has no result stored, which beats the clock even at a heavy hour. Otherwise it
is the clock schedule. Outside both it answers `200 skipped` and runs nothing.

```powershell
$token = Read-Host "Token"
try {
    Invoke-RestMethod -Method Post `
        -Uri "https://cfb-intelligence.onrender.com/internal/cfb-refresh?profile=auto" `
        -Headers @{ Authorization = "Bearer $token" } | ConvertTo-Json
} catch {
    "HTTP " + $_.Exception.Response.StatusCode.value__
}
```

The `try`/`catch` matters on Windows PowerShell 5.1: `Invoke-RestMethod` throws
on any non-2xx, so without it a 401 arrives as a red exception rather than a
status you can read. (`-SkipHttpErrorCheck` does the same job on PowerShell 7+.)

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "https://cfb-intelligence.onrender.com/internal/cfb-refresh?profile=auto"
```

Reading the answer:

| Code | Means |
| --- | --- |
| `202` | Started. Not finished — check the status endpoint or the log. |
| `200` `"skipped"` | This moment calls for nothing. Correct outside 6/12/18/23 Eastern with no game in play. |
| `401` | Token mismatch, or the web service is still redeploying. |
| `503` | `CFB_REFRESH_TOKEN` is not set on the web service. |

### Rotating the token

It is a plain shared secret compared with `secrets.compare_digest`; nothing is
derived from it, so rotating it is changing the same string in two places.

1. `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Render → **`cfb_intelligence`** (web) → Environment → `CFB_REFRESH_TOKEN` → Save.
   This redeploys the service.
3. Render → **`cfb-refresh-trigger`** (cron) → Environment → same value → Save.
4. Verify with the trigger above.

Do 2 and 3 back to back. Between them the cron gets a 401; since it fires every
quarter hour, a missed one costs nothing. Update your local `.env` too if the
old value is in it.

---

## Reclaiming disk

Dry run unless a tier is named. Ordered by what it costs you, not what it
recovers.

```bash
python -m sports_aggregator.cfb.prune_cli                        # report only
python -m sports_aggregator.cfb.prune_cli --apply raw --vacuum
python -m sports_aggregator.cfb.prune_cli --content-days 180 --apply reporting --vacuum
python -m sports_aggregator.cfb.prune_cli --before-season 2021 --apply seasons --vacuum
```

| tier | removes | recover by |
| --- | --- | --- |
| `raw` | ingest payloads nothing reads | nothing is lost |
| `reporting` | articles past the window and their links | re-ingesting |
| `seasons` | box scores before a cutoff | `bootstrap history` |

Deleting leaves free pages *inside* the file — without `--vacuum` the database
does not shrink on disk. Vacuum rewrites the file and needs free space equal to
its current size.

---

## Inspecting the database

```bash
sqlite3 /var/data/cfb.sqlite3 "SELECT COUNT(*) FROM content_items;"
sqlite3 /var/data/cfb.sqlite3 ".tables"
sqlite3 /var/data/cfb.sqlite3 ".schema games"
```

Without the `sqlite3` binary:

```bash
python -c "import sqlite3; d=sqlite3.connect('/var/data/cfb.sqlite3'); \
print(d.execute('SELECT COUNT(*) FROM content_items').fetchone())"
```

---

## Git

```bash
git status -sb
git fetch origin && git rev-list --left-right --count HEAD...origin/main   # ahead / behind

git add -A && git commit -m "message"
git push origin main

git fetch origin && git rebase origin/main        # when main has moved
git log --oneline -10
git diff origin/main...HEAD --stat                # what this branch adds
```

Restoring a deleted branch, if you know its tip:

```bash
git push origin <sha>:refs/heads/<branch-name>
```

---

## Syntax differences

The commands that bite most often.

| task | PowerShell | bash |
| --- | --- | --- |
| set a variable | `$token = "abc"` | `TOKEN='abc'` (no spaces around `=`) |
| use a variable | `$token` | `$TOKEN` |
| environment variable | `$env:NAME = "x"` | `export NAME=x` |
| line continuation | backtick `` ` `` | backslash `\` |
| discard output | `> $null` | `> /dev/null` |
| last 20 lines | `Get-Content f -Tail 20` | `tail -20 f` |
| find a process | `Get-Process` | `ps aux` |
| kill a process | `Stop-Process -Id N -Force` | `kill -9 N` |
| chain on success | `A; if ($?) { B }` | `A && B` |

`Invoke-RestMethod`, `Get-Content` and `Stop-Process` do not exist in bash.
`curl`, `tail`, `grep` and `kill` do not behave the same way in PowerShell —
`curl` there is an alias for `Invoke-WebRequest`, which takes different
arguments.

---

## Environment variables

| name | purpose | default |
| --- | --- | --- |
| `CFB_DATABASE_PATH` | database location | `instance/cfb.sqlite3` |
| `CFB_DEFAULT_SEASON` | season the site shows | current year |
| `CFB_REFRESH_TOKEN` | auth for `/internal/*` | unset — those endpoints return 503 |
| `CFB_PAGE_CACHE_SECONDS` | rendered-page lifetime; `0` disables | 900 |
| `CFB_REFRESH_CHILD_MB` | memory ceiling per refresh step; `0` disables | 320 |
| `REGISTER_LEGACY_DASHBOARDS` | load the Reds/Bengals stack | off on Render |
| `FLASK_DEBUG` | debug tracebacks on the local server | `1` |

Local values live in `.env`; Render values live in the dashboard under
Environment.
