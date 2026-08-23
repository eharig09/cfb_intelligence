# College Football Intelligence Architecture

## Organizing principle

The college-football system is built around:

`Teams → Players → Games → Reporting`

Phase 1 establishes teams and games as canonical CFBD-backed entities. Reporting
already has normalized source metadata, while player and reporting relationship
tables belong to the next incremental phases.

## Data flow

```text
CFBD REST API
    ↓ authenticated GET + retry policy
Raw JSON cache (instance/cfbd_raw)
    ↓ endpoint-specific normalization
Canonical SQLite store (instance/cfb.sqlite3)
    ↓ repository queries
College Football Today / game pages / JSON API

RSS and future reporting adapters
    ↓ normalized Article + reliability tier
Deduplication and short cache
    ↓
College Football Today reporting section
```

Network schemas do not cross into templates. CFBD adapters return raw payloads,
normalizers create canonical entities, repositories persist them, and query/view
code deals only with canonical fields.

## Current canonical tables

- `teams`: CFBD team ID, school metadata, conference, colors, logos, and venue.
- `team_aliases`: normalized alias candidates keyed to canonical CFBD team IDs.
  Mascot-only aliases may be ambiguous by design; callers receive all candidates.
- `players`: season-specific CFBD rosters keyed by stable player ID.
- `player_season_stats`: long-form CFBD player production by season, canonical
  conference name, team, category, and stat type.
- `player_transfers`: current CFBD portal entries with origin, destination, date,
  rating, stars, and eligibility status.
- `draft_picks`: NFL Draft destination and round/pick data linked by CFBD athlete ID
  where available, with exact normalized-name fallback for display.
- `returning_production`: preseason team PPA/usage continuity signals.
- `games`: CFBD game ID, team IDs, schedule/result fields, Elo, venue, and attached
  media outlet.
- `team_records`: seasonal overall and conference W-L-T records for FBS teams.
- `rankings`: poll snapshots keyed by school, allowing tied numeric ranks.
- `team_stats`: long-form basic seasonal statistics.
- `team_advanced_stats`: queryable core fields plus retained offense/defense JSON.
- `core_ratings`: current CFBD CORE rating snapshots.
- `sync_runs`: auditable per-dataset counts and status without credentials.

Normalized content, source entities, story clusters, and their team/player/game
relationships live in the social schema in this same SQLite store. Relationships
remain join tables rather than arrays embedded in content rows.

## Verified CFBD contract (2026-08-23)

The implementation uses the current documented production base URL and query
names, not the deprecated Swagger schema:

- `GET /teams/fbs?year=`
- `GET /conferences?year=`
- `GET /games?year=&seasonType=both&classification=fbs`
- `GET /games/media?year=&seasonType=both&classification=fbs`
- `GET /records?year=` (filtered to `classification=fbs` during normalization)
- `GET /rankings?year=`
- `GET /stats/season?year=&classification=fbs`
- `GET /stats/player/season?year=&conference=`
- `GET /player/portal?year=`
- `GET /player/returning?year=`
- `GET /draft/picks?year=`
- `GET /stats/season/advanced?year=&classification=fbs&excludeGarbageTime=true`
- `GET /ratings/core?year=`

Live 2026 verification returned 138 FBS teams, 888 games, 436 game/media mappings,
138 FBS records, and 75 poll rows. Basic/advanced/CORE season outputs were empty at
the time of verification because the 2026 regular season had not produced data.

## Cache policy

- Team metadata: 7 days
- Schedule/results: 15 minutes
- Media and CORE ratings: 1 hour
- Records and rankings: 30 minutes
- Basic and advanced seasonal statistics: 6 hours
- Player seasonal statistics: 6 hours; synchronized conference-by-conference

Every successful response is preserved as a raw JSON envelope with endpoint,
parameters, and fetch time. API credentials are headers only and are never written
to cache metadata or logs.

## Reporting reliability

Normalized articles now carry `source_type`, a 1–5 reliability tier, and empty
canonical team/player/game ID relationships ready for entity resolution. ESPN is
configured as Tier 4 national reporting. Future official team/conference feeds
should be Tier 5 and clearly labeled as official content rather than independent
journalism.

## Presentation contract

Statistics are stored long-form and displayed wide. The store keeps one row per
(season, category, stat_type) because that is what CFBD publishes and what
ingestion can upsert safely; the pages pivot those rows into box-score lines at
read time in `cfb/statlines.py`. Nothing denormalizes the store to make a page
easier to write.

Formatting is a column property, not a template decision:

- `pct` is a value already on a 0-100 scale.
- `rate` is a 0-1 fraction and is always multiplied by 100.
- `int`, `big`, `f1`, `f2`, `f3`, and `num` cover counts and decimals.
- Absent values render as an em dash, never as a zero or a blank cell.

Any packet that carries a `format` field, such as the preseason team-quality
cards, must use these names. Two different scales previously shared the label
`percent`, and the pages guessed between them.

Leaderboards rank on one statistic but return the whole category stat line, and
each category declares a qualifying minimum in `LEADER_QUALIFIERS`. Ranking on a
volume statistic without a minimum let a single trick-play attempt outrank real
production.

## Elo

CFBD publishes Elo on the game record (`homePregameElo` / `awayPregameElo`), not
as a team-level table, and only for games close enough to have been rated. Each
team therefore takes the rating from its most recent rated game, and the week
that rating came from travels with it. All 138 FBS teams currently resolve from
week-1 ratings. It is shown in conference standings alongside record and expected
wins, with its national rank among rated teams.

## Draft board

The 2027 board has two independent inputs that are deliberately never blended:

1. A **production profile** calibrated on the completed 2026 draft. 247 of its
   257 picks match by name and school to a prior-season PFF profile, giving a
   real per-position distribution of what a drafted player looked like a year
   earlier. Returners are placed against it as a percentile.
2. An **imported consensus board**, stored whole with its own rank, source file,
   and per-row identity status. 97 of 100 rows link to a roster player; the
   importer matches ignoring generational suffixes and carries an explicit,
   reviewable map for school names no CFBD alias covers.

Reconciliation reports agreement, board-ahead-of-profile, profile-ahead-of-board,
and no-profile as four separate groups. A freshman with no prior-season sample is
missing evidence, not a disagreement, and is never presented as one.

Draft eligibility is inferred from class year, the only signal the CFBD roster
carries. Redshirts and early declarations are invisible to it, so it is labeled
an estimate wherever it appears.

## Immediate next increment

1. Persist rosters and player IDs for the active season.
2. Add contextual team alias resolution and player-team lookup.
3. Adapt existing Reddit, Bluesky, and CBS ingestion into normalized records.
4. Compute article-to-team candidates with explanations.
5. Match candidates to upcoming CFBD game IDs and store score factors.

Only after those links exist should the preview system generate matchup edges,
players to know, prospect context, and award context.
