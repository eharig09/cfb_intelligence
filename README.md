# Sports News Aggregator

A Flask sports dashboard evolving from Cincinnati team pages into a reusable,
multi-league aggregation platform. College football is the first league on the
new shared pipeline.

## Current architecture

The active application factory is `app.create_app`. It exposes:

- `/` — league directory plus links to legacy dashboards
- `/college-football/` — College Football Today dashboard
- `/leagues/college-football/` — redirect to the CFB dashboard
- `/college-football/conferences/<slug>/` — conference stories, standings, games,
  current player leaders, and historical PFF context
- `/college-football/teams/<team_id>/` — full team schedule, roster, production,
  news, experience-first depth board, arrivals/departures, and prior-season
  player/position-group context
- `/college-football/players/<player_id>/` — player career path, season statistics,
  confirmed PFF grades, transfer/draft events, and player/team reporting
- `/college-football/games/<game_id>/` — full matchup preview with current CFBD
  metrics, PFF unit comparisons, players to know, and attributed story clusters
- `/api/v1/leagues` — machine-readable league discovery
- `/api/v1/leagues/college-football/articles` — normalized article API
- `/api/v1/cfb/status` — structured-data freshness and row counts
- `/api/v1/cfb/games` — upcoming canonical CFBD games
- `/api/v1/cfb/games-to-watch` — scored upcoming games with explanation factors
- `/api/v1/cfb/teams` and `/api/v1/cfb/rankings` — canonical discovery data
- `/api/v1/cfb/conferences` and `/api/v1/cfb/conferences/<slug>` — conference discovery and view packets
- `/api/v1/cfb/teams/<team_id>` — team preview packet
- `/api/v1/cfb/players/<player_id>` — player identity, statistics, career, and stories
- `/api/v1/cfb/games/<game_id>/preview` — full game preview packet
- `/api/v1/cfb/teams/resolve?q=...` — exact normalized alias candidates
- `/college-football/admin/sources/` — curated Bluesky identity and coverage review
- `/college-football/admin/source-graph/` — unified entities, endpoints, and candidates
- `/api/v1/cfb/sources` — source metadata, DID status, and explicit coverage gaps
- `/api/v1/cfb/source-entities` — platform-neutral source graph
- `/api/v1/cfb/content` — normalized recent source content without raw payloads
- `/api/v1/cfb/games/<game_id>/content` — reporting layers for a scheduled game
- `/api/v1/cfb/games/<game_id>/matchups` — ranked unit matchups with reasons
- `/api/v1/cfb/developments` — content ranked by relevance rather than recency
- `/college-football/draft/` — 2027 draft watch: consensus board vs production profile
- `/api/v1/cfb/draft/board`, `/draft/consensus`, `/draft/reconcile` — draft packets
- `/college-football/search/` — cross-entity search over teams, players, games, reporting
- `/college-football/admin/links/` — entity link audit with matched text and rule
- `/api/v1/cfb/links` — the same audit as JSON
- `/api/v1/cfb/games/<game_id>/player-matchups` — individual matchups in a game
- `/api/v1/cfb/games/<game_id>/situation` — schedule spot, travel, availability, market
- `/api/v1/cfb/games/<game_id>/weather`, `/fpi` — kickoff forecast and FPI packets
- `/api/v1/cfb/sources/status` — row counts, freshness and failures per source
- `/api/v1/cfb/search`, `/api/v1/cfb/transfers` — search and portal-impact packets
- `/api/v1/cfb/pff/summary?season=2025` — historical player and position-group signals
- `/reds/` and `/bengals/` — existing team dashboards, unchanged

The new `sports_aggregator/` package has four boundaries:

1. `models.py` defines stable provider-neutral data contracts.
2. `providers/` converts RSS, APIs, scrapers, or social sources into those contracts.
3. `service.py` runs sources concurrently, isolates failures, deduplicates results,
   sorts them, and caches each league briefly.
4. `catalog.py` declares leagues and their sources; `web.py` delivers the same data
   through HTML and JSON.

Presentation is its own boundary rather than template logic:

- `sports_aggregator/tables.py` defines `Column` and `Table`: a column names its
  key, header, and format once, and one Jinja macro decides markup, alignment,
  and number formatting. `pct` means 0-100 and `rate` means a 0-1 fraction, so a
  percentage cannot render at two different scales on two pages.
- `sports_aggregator/cfb/statlines.py` pivots the long-form
  `player_season_stats` store into conventional box-score lines. Column order,
  headers, and leaderboard qualifying minimums live in one spec per category.
- `sports_aggregator/cfb/views.py` turns repository packets into `Table`
  objects for schedules, standings, leaders, depth boards, roster movement,
  PFF grades, matchup metrics, and ranked developments. Team tables carry the
  school logo and color through the `_logo` / `_color` row conventions.
- `sports_aggregator/cfb/matchups.py` ranks unit-versus-unit comparisons by how
  watchable they are — quality, separation, and mutual strength — and labels
  each one. It consumes a provider-neutral `MatchupSignal`, so compiled season
  statistics and models can feed the same ranking without touching callers.
- `sports_aggregator/providers/sportsdataverse.py` downloads static SportsDataverse
  release assets, resolving URLs through the release API and validating that an
  asset actually carries the expected columns before it is trusted.
- `sports_aggregator/providers/weather.py` fetches Open-Meteo forecasts per venue
  and aligns them to kickoff. No API key is required.
- `sports_aggregator/cfb/external.py` stores secondary sources keyed to canonical
  CFBD entities, with provenance on every row and `import_runs` recording every
  attempt. See [docs/SECONDARY_SOURCES.md](docs/SECONDARY_SOURCES.md).
- `sports_aggregator/bootstrap.py` is the single entry point: `initial`,
  `refresh`, `status` and `plan`. Each step runs isolated so one unavailable
  source cannot stop the rest.
- `sports_aggregator/cfb/roster_production.py` splits prior-season production
  into returning, arrived and departed, so a preseason page says what is on the
  roster rather than who led the team last year. Arrived production always names
  the school it was earned at.
- `sports_aggregator/cfb/search.py` matches a query against team aliases, person
  names, matchups and headlines, and returns why each result matched.
- `sports_aggregator/cfb/lines.py` stores betting lines per provider with opening
  and current numbers kept apart. Books are never averaged into one number.
- `sports_aggregator/cfb/situations.py` derives schedule spots, travel and
  time-zone burden, and availability reporting for a game.
- `sports_aggregator/cfb/transfers.py` ranks portal entries on prior production
  first, grade second and recruiting opinion last. A transfer with no record is
  reported as unproven, which is not the same as low impact.
- `sports_aggregator/social/team_reddit.py` keeps team subreddits in a registry
  and activates them by the week's schedule rather than sweeping all 138.
- `sports_aggregator/cfb/player_matchups.py` pairs graded individuals across the
  line of scrimmage and ranks the pairings. Both sides must grade well; a place
  on the consensus board raises a pairing but cannot create one.
- `sports_aggregator/social/context.py` gates the widest resolution rule. An
  unscoped player match is blocked by professional-football vocabulary and by a
  coaching title beside the name, because shared names across levels were the
  main source of wrong links.
- `sports_aggregator/cfb/identity.py` derives a contrast-checked accent for each
  team from its CFBD color and holds the editorial conference palette. Team
  colors are chosen for helmets, so a near-white one is darkened only as far as
  it must be to stay visible rather than replaced.
- `sports_aggregator/social/roles.py` decides what an item *is* — original report,
  corroboration, analysis, opinion — from markers in the text plus its position in
  a story cluster, and keeps the evidence for every verdict. It replaces the
  `REPORTING_UNDETERMINED` placeholder that every journalist's post received.
- `sports_aggregator/cfb/draft.py` builds a prospect board calibrated on the
  completed draft: 247 of the 2026 picks are matched to their prior-season PFF
  profile, and returners are placed against that distribution by position.
- `sports_aggregator/cfb/prospects.py` imports an external consensus board with
  provenance and reconciles it against that profile board, reporting agreement,
  disagreement, and missing evidence separately.
- `sports_aggregator/social/media.py` validates YouTube channels and podcast
  feeds before they can enter the trusted registry, and attaches both endpoints
  to the same show entity so one programme distributed twice does not gain
  double authority.
- `sports_aggregator/social/relevance.py` scores content on source expertise for
  the topic at hand, role, topic importance, recency half-life, and how
  specifically the item resolved to a team, player, or game. Every score keeps
  its factors as text.
- `templates/_layout.html`, `templates/_tables.html`, and `static/cfb.css` are
  the shared page shell, table macros, and stylesheet. Pages previously carried
  a private copy of the same CSS.

The `sports_aggregator/cfb/` package adds the structured college-football path:

- `cfbd.py` is the authenticated current CFBD REST adapter with retries and a raw
  response cache.
- `models.py` maps CFBD team/game IDs into provider-neutral entities.
- `repository.py` owns the normalized SQLite schema and safe upserts.
- `sync.py` isolates each weekly dataset so one unavailable access tier does not
  discard successful updates.
- `insights.py` contains an intentionally provisional, explainable game-attention
  score. It is not presented as the final importance model.
- `pff.py` imports the user-provided 2025 PFF snapshots with source-file provenance,
  conservative player matching, and usage-weighted position-group summaries.

The parallel `sports_aggregator/social/` package is the curated reporting boundary.
It models people, publications, shows, organizations, and communities once, then
attaches Bluesky, Reddit, RSS, API, YouTube, and podcast endpoints. It also stores
multidimensional expertise, resolves stable platform identities, persists normalized
content, and conservatively attaches topics, teams, players, and games. The legacy
Reds and Bengals integrations remain unchanged.

The initial source is ESPN's documented college-football RSS feed. Provider terms
still apply: preserve attribution and links, and do not modify syndicated content.

## Run locally

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Add `CFBD_API_KEY` to `.env`, then build or refresh the 2026 structured store:

```powershell
python -m sports_aggregator.cfb.cli sync --year 2026
python -m sports_aggregator.cfb.cli sync-player-stats --year 2025
python -m sports_aggregator.cfb.cli sync-roster-context --year 2026
python -m sports_aggregator.cfb.cli status --year 2026
```

Backfill prior seasons so a player page shows a full career rather than one year.
Careers span roughly five seasons, so a current senior was a freshman well outside
the active window:

```powershell
python -m sports_aggregator.cfb.cli backfill --year 2026 --from-year 2019 --to-year 2024
```

A team promoted from FCS has no history in any FBS-filtered dataset. `sync-promoted`
finds those teams and fetches their prior seasons from the conference they actually
played in; `coverage` reports which conference-seasons are missing:

```powershell
python -m sports_aggregator.cfb.cli sync-promoted --year 2026 --from-year 2024 --to-year 2025
python -m sports_aggregator.cfb.cli sync-venues --year 2026
python -m sports_aggregator.cfb.cli sync-lines --year 2026
python -m sports_aggregator.cfb.cli sync-recruits --year 2026
python -m sports_aggregator.cfb.cli link-transfer-grades --year 2026
python -m sports_aggregator.cfb.cli coverage
```

Use `--force` only when intentionally bypassing the raw-response cache. Use
`--basic` to omit advanced statistics and CORE ratings. The equivalent Flask CLI
command is `flask --app app sync-cfb --year 2026`.

The player-stat sync resolves CFBD conference display names to its official API
abbreviations. Use `--conference "Big Ten"` to refresh one conference. During
preseason, the UI automatically falls back to the newest available prior-season
player statistics and labels that season explicitly.

`sync-roster-context` loads the prior roster plus current transfer portal, NFL Draft,
and returning-production datasets. Team pages identify sourced transfers/draft picks,
infer possible graduation or eligibility departures only from class/roster comparison,
and label that inference instead of presenting it as confirmed reporting.

Import the local 2025 PFF snapshot after a 2026 roster sync:

```powershell
python -m sports_aggregator.cfb.pff_cli import --season 2025 --roster-season 2026 --directory PFF
```

Seed and verify the curated Bluesky registry:

```powershell
python -m sports_aggregator.social.cli seed
python -m sports_aggregator.social.cli resolve
python -m sports_aggregator.social.cli status
python -m sports_aggregator.social.cli prepare
python -m sports_aggregator.social.cli validate-reddit
python -m sports_aggregator.social.cli unified-status
```

Resolution is intentionally strict: both the handle resolver and actor profile must
agree on the DID/current handle. A transient failure does not erase a previously
verified DID.

Run the public Bluesky author-feed ingestion as a background/scheduled command:

```powershell
python -m sports_aggregator.social.content_cli ingest --season 2026 --limit 10
python -m sports_aggregator.social.content_cli ingest-reddit --season 2026 --limit 25
python -m sports_aggregator.social.content_cli ingest-youtube --season 2026 --limit 20
python -m sports_aggregator.social.content_cli ingest-podcasts --season 2026 --limit 20
python -m sports_aggregator.social.content_cli ingest-reporting --season 2026
python -m sports_aggregator.social.content_cli cluster
python -m sports_aggregator.social.content_cli score
python -m sports_aggregator.cfb.prospects_cli 2027_nfl_mock_draft_database_top_100.csv     --draft-year 2027 --roster-season 2026 --source mock_draft_database_consensus
python -m sports_aggregator.social.content_cli status --limit 50
```

`ingest-reddit` needs `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and
`REDDIT_USER_AGENT`; the unauthenticated endpoint is blocked. Submissions are
classified (`LINK_DISCOVERY`, `GAME_THREAD`, `ANALYSIS`, `RUMOR`, …) and a
submission that links out credits the external publisher, keeping the subreddit
only as the discovery endpoint.

`retag` re-runs topic and entity resolution over stored content without
re-fetching any source, so a tagging-rule change reaches the whole archive:

```powershell
python -m sports_aggregator.social.content_cli retag --season 2026
```

Set `YOUTUBE_API_KEY` (or `YOUTUBE_API`, which is also accepted) before validating
or polling any YouTube candidate. Candidate names are not production endpoints:
channel IDs and podcast feeds are discovered through search, scored on name
agreement, audience, publishing history, and topical vocabulary, and only clear
matches are promoted. Search rank never decides identity — a query for "Split Zone
Duo" returns a four-subscriber channel above the real show, and one for "Joel Klatt
Show" returns a channel with no videos.

Of the eight seeded show candidates, six YouTube channels and five podcast feeds
passed validation on 2026-08-23. Andy Staples (who publishes on the On3 channel)
and College Football Enquirer (a Yahoo Sports programme) have no dedicated channel
and remain in review rather than being attached to a parent brand.

To run only the lightweight shared league platform while legacy pages are being
migrated:

```powershell
$env:REGISTER_LEGACY_DASHBOARDS = "0"
python run.py
```

For production, set `FLASK_DEBUG=0`, keep secrets outside source control, use a
real shared cache when running multiple workers, and run the app behind a WSGI
server.

## Add another league

For an RSS-backed league, add a `LeagueConfig` and its `FeedConfig` values to
`sports_aggregator/catalog.py`. Discovery, the league page, caching, aggregation,
and JSON endpoints are automatic.

For a non-RSS source:

1. Implement the small `NewsProvider` protocol in `sports_aggregator/providers/`.
2. Normalize every record to `Article`.
3. Register the provider in `build_default_service`.
4. Add parsing and failure-isolation tests.

Avoid putting provider request code in Flask views. That separation is what makes
the same source usable by web requests, scheduled ingestion, and future workers.

## Repository assessment

The prototype contains useful integrations, but they sit at different stages of
refactoring:

- `reds/` already separates scrapers, stats, social, charts, and utilities, but its
  public models and orchestration remain Reds-specific.
- `blueprints/bengals.py` is a large module with repeated aggregation/authentication
  definitions and source-specific parsing mixed into the route layer.
- `blueprints/reds.py` appears to be an older duplicate of the active `reds/`
  package and should be retired after behavior is compared.
- `nfl_prospect_scraper_v5.py` is a capable standalone search CLI. Its source
  functions are candidates for provider adapters, but draft-prospect search should
  remain a separate feature from the general league headline feed.
- Generated charts, a local virtual environment, secrets, and application code
  currently share the repository root. `.gitignore` and `.env.example` now define
  the intended boundary; existing local files were not removed.
- The previous `routes.py` contained an invalid demo callback and a second Flask
  app. It is now only a compatibility entry point to the canonical factory.

## Recommended development sequence

Phase 1 is now implemented: current CFBD teams, games, media, overall/conference records, poll
rankings, basic team statistics, advanced statistics, and CORE ratings have
cache/persistence adapters. Empty preseason datasets are valid and will populate
as CFBD publishes in-season data.

The unified source graph, Reddit discovery normalization, durable Bluesky ingestion,
multilabel topic rules, conservative team/player/game candidates, and game-page
reporting layers are implemented. Cross-source story clustering now preserves
source roles and treats earliest-report attribution as a confidence-scored candidate,
not a fact. Conference hubs plus full team and game preview shells consume the same
repository packets in HTML and JSON. The next sequence is:

1. **Candidate validation:** resolve exact YouTube channel IDs and podcast feeds,
   then promote only identities that pass live and editorial checks.
2. **Official-source expansion:** validate athletics/conference endpoints and add
   game notes, releases, press conferences, and official video.
3. **Scoring:** apply topic expertise, team access, originality, recency, and game
   relevance after classification precision has been measured.
4. **Scheduling:** run source polling outside web requests, dynamically increasing
   frequency for teams and games that matter that week.
5. **Migrate legacy pages:** adapt Reds and Bengals sources one at a time, then
   remove the duplicate modules after parity tests pass.
6. **Production hardening:** add database migrations, structured logging, request
   timeouts/retries, source rate limits, health checks, and CI.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The 217 tests cover normalization, RSS/Reddit/YouTube/podcast adapters, external
publisher credit, provider failure isolation, source-graph identity, content topics,
team/player matching, story clusters and source roles, CFBD authentication and caching,
conference standings/player leaders, roster lifecycle classification, PFF import,
SQLite persistence, conference/team/player/game HTML routes, story destinations,
JSON view packets, limits, and 404 behavior. `tests/test_tables.py` additionally
covers cell formatting and scale, stat-line pivoting and column order, leaderboard
qualifying minimums, schedule win/loss derivation, and the rendered player page
emitting a real pivoted table. `tests/test_relevance.py` covers topic half-lives,
expertise selection, beat-versus-pundit ordering, Reddit crosspost detection and
publisher credit, matchup archetypes and sample discounting, and score
persistence. `tests/test_media.py` covers YouTube key naming, channel and feed
validation including impersonator and unrelated-brand rejection, video
classification, the team resolver (three-letter programmes, case sensitivity,
lead prominence, and roundup demotion), and the per-source streams.
`tests/test_draft.py` covers role determination and its precedence rules, board
parsing, name-suffix and school-alias matching, percentile calibration, and the
separation of missing evidence from genuine disagreement.
`tests/test_presentation.py` covers headline extraction from promotional video
descriptions, color contrast and conference palette readability, position
abbreviations, the separation of team reporting from conference context, and the
mobile-first stylesheet rules, individual matchup pairing and its draft
weighting, the link-audit shape, the unscoped-match guard, initial-collapsing name
normalization, and statistical coverage-gap reporting. `tests/test_features.py` covers search
scoring and abbreviated school names, per-provider line storage and movement,
travel and time-zone derivation, transfer impact evidence, and team-subreddit
activation.

See [docs/CFB_ARCHITECTURE.md](docs/CFB_ARCHITECTURE.md) for schema ownership,
cache policy, verified CFBD endpoints, and the next entity-linking boundary.
See [docs/CFB_NEWS_AGGREGATION.md](docs/CFB_NEWS_AGGREGATION.md) for the Bluesky
registry assessment, PFF identity policy, and incremental news roadmap.
