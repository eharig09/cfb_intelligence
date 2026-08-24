# CFB podcast and YouTube registry

The versioned research artifact is
[`data/media/cfb_media_registry.json`](../data/media/cfb_media_registry.json). It
keeps four distinct outputs: preserved seed sources, newly researched sources,
seed enrichment, and remaining team-level coverage gaps. The application imports
the first three as one deduplicated candidate catalog; gap rows are research
backlog, not ingestible endpoints.

Each show is one source entity. Podcast RSS, YouTube, and website locations are
endpoints on that entity, so the same episode distributed on multiple platforms
does not count as independent corroboration.

## Refresh behavior

`bootstrap initial` and `bootstrap refresh` both run `media-seed` before
`media-validate`. Seeding is idempotent and does not erase an existing verified
endpoint. Failed or ambiguous platform identities are retried after seven days,
which protects the YouTube search quota and avoids repeatedly fetching every
directory candidate throughout the day. Use `--force` for an intentional manual
retry.

```powershell
python -m sports_aggregator.social.media_cli seed
python -m sports_aggregator.social.media_cli validate-podcasts
python -m sports_aggregator.social.media_cli validate-podcasts --exact-only
python -m sports_aggregator.social.media_cli validate-youtube
python -m sports_aggregator.social.media_cli validate-all --force
python -m sports_aggregator.social.media_cli status
```

YouTube validation is skipped cleanly when neither `YOUTUBE_API_KEY` nor
`YOUTUBE_API` is configured. Podcast validation does not need a credential.

## Identity and quality policy

- Exact RSS feeds in the catalog are fetched and checked against their own feed
  title before promotion. Apple show pages are provenance links, not ingestion
  endpoints.
- Search rank never establishes YouTube identity. Search-discovered channels need
  strong name agreement, publishing history, topical evidence, and the existing
  audience corroborator.
- A researched stable channel identity may be small. It is not rejected merely
  for having fewer than 5,000 subscribers; it still needs matching identity and
  publishing history.
- `original_reporting` and `program_access` are evidence fields. They raise source
  expertise only after the catalog contains a concrete reporting rationale.
- `seasonal_review` sources must publish recently before they are promoted.
- Team and conference assignments use the application's 2026 alignment (for
  example, Northern Illinois is Mountain West and Texas State is Pac-12).

## Continuing research

Work from `remaining_coverage_gaps`, prioritizing a local beat show before adding
another national generalist. Never guess a feed, channel ID, subscriber count, or
access claim. Add an Apple/show page as provenance, but put only a verified RSS
feed in `podcast_url`. After editing the JSON, run the media tests and `seed`; the
loader rejects duplicate names, unknown tags/platforms, and invalid priorities.
