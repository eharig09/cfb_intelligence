# Nationwide local college-football source registry

The local-source registry supplements structured CFB data with reporting that can
surface injuries, availability, practice developments, depth-chart changes,
transfers, recruiting, coaching, scheme, travel, weather, and other team-specific
signals before they become national stories.

## Deliverables

Generated production artifacts live in `data/local_sources/`:

- `cfb_local_source_registry.json` is the complete denormalized team registry.
- `cfb_local_publishers.json` normalizes unique publishers and their team beats.
- `cfb_local_source_summary.csv` is the requested team-by-team summary table.
- `cfb_local_machine_endpoints.json` lists every verified Google News RSS,
  publisher-native RSS/Atom feed, sports feed, and news sitemap.
- `cfb_local_coverage_report.json` contains aggregate coverage counts and weak teams.
- `cfb_local_problem_cases.json` records sparse coverage, ambiguous names, missing
  native feeds, unknown paywall status, and endpoint errors.

Every unknown endpoint field is `null`; the generator never invents a native RSS,
API, team page, or sitemap URL.

## Verification method

The FBS inventory is read from the canonical `teams` table, which currently has
138 programs under the 2026 conference alignment. For every program:

1. Build safe aliases, with explicit disambiguation for Miami, Miami (OH), USC,
   UTSA, and UMass/Massachusetts.
2. Run a team/geography query over a 365-day season cycle.
3. Exclude national publishers, aggregators, fan networks, official athletics
   sites, and known irrelevant domains from local-source promotion.
4. Run a second query constrained to the candidate publisher's domain.
5. Require at least two returned headlines that name the team or a safe alias and
   also contain football-specific vocabulary.
6. Infer each publisher's dominant reporting state from its full verified evidence
   and remove weak cross-state opponent coverage. Every removal remains in
   `metadata.geographic_outliers_removed` with both evidence counts.
7. Inspect each retained publisher homepage for declared RSS/Atom links and verify
   that each feed parses with entries. Inspect only `robots.txt`-declared news
   sitemaps. Common guessed paths such as `/feed` are never probed.

Google News is a discovery and fallback transport. The source entity remains the
original local publisher, and every fallback query is constrained to that domain.

## Current FBS coverage

The August 2026 build researched all 138 FBS programs:

- 104 teams have three verified sources.
- 13 have two sources.
- 12 have one source.
- 9 have no source meeting the recurring-coverage floor.
- 67 teams have at least one verified publisher-native RSS/Atom feed.
- 129 teams have verified publisher-constrained Google News RSS fallbacks.
- 77 weak cross-state source/team mappings were rejected by the geographic audit.

The empty or weak teams are intentionally retained as problem cases rather than
filled with one-off opponent coverage. Paywall status and original-reporting status
remain `null` where the automated evidence cannot establish them safely.

## Commands

Research and write all deliverables:

```powershell
python -m sports_aggregator.social.local_sources_cli research `
  --classification fbs --days 365 --sources-per-team 3 `
  --max-workers 10 --output-dir data/local_sources
```

Verify publisher-declared native endpoints without repeating source discovery:

```powershell
python -m sports_aggregator.social.local_sources_cli enrich `
  --output-dir data/local_sources
```

Import publishers, team beats, and verified endpoints into the unified source graph:

```powershell
python -m sports_aggregator.social.local_sources_cli import `
  --output-dir data/local_sources
```

Ingest team-scoped local articles:

```powershell
python -m sports_aggregator.social.content_cli ingest-local-reporting `
  --season 2026 --limit 15
```

The ingestion command combines duplicate article identities and unions their team
IDs before storage. Existing topic classification and entity resolution then label
injury, practice, depth chart, availability, suspension, transfer, recruiting,
coaching, scheme, weather, travel, betting, quote, preview, recap, and general news.

`bootstrap initial` imports the committed registry during source preparation.
Both `initial` and `refresh` ingest local reporting after national RSS and before
retagging, clustering, and relevance scoring.

## FCS extension

The same pipeline supports `--classification fcs`. FCS should be researched into a
separate reviewed artifact before merging because local coverage is thinner and
school-name ambiguity is more common. FBS quality is not weakened to inflate FCS
coverage.

