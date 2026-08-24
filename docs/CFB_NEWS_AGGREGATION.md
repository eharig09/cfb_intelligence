# College-football news and player-intelligence preparation

## Implemented boundary

The existing social integrations are account-list implementations scoped to the
legacy dashboards. `reds/social/__init__.py` authenticates with a Bluesky username
and password and requests author feeds from a Reds-specific list;
`blueprints/bengals.py` contains another hard-coded implementation. Neither had a
shared source identity, expertise, DID, health, or persistence layer.

Those paths remain untouched. The new `sports_aggregator/social/` package is a
parallel league-neutral boundary with:

- 41 user-curated CFB seeds and multidimensional reporting/analysis scores;
- normalized source specialties plus explicit team and conference relationships;
- unauthenticated AT Protocol handle and profile lookup;
- canonical DID persistence only after bidirectional handle/profile validation;
- resolution timestamps/status and failure retention;
- a current-source and coverage-gap admin page and JSON API.

The registry now also projects those compatibility rows into a platform-neutral
graph:

```text
source_entities
  ├── source_endpoints
  ├── source_entity_classes
  ├── source_entity_specialties
  ├── source_entity_teams
  ├── source_entity_conferences
  └── source_relationships
```

The projection is idempotent. One source entity can own multiple endpoints without
receiving duplicate authority. Exact organization-name matches currently create 13
reporter-to-publication relationships; compound or uncertain affiliations are not
guessed.

The live validation on 2026-08-23 verified 40 seeds. `skhanjr.bsky.social` returned
HTTP 400 from the public handle resolver and remains visible as
`resolution_failed`. No substitute handle was guessed or added.

## PFF snapshot policy

The seven summary CSVs plus corrected regular-season detail exports under `PFF/`
are treated as a licensed historical 2025 snapshot, not as current 2026 production
and not as a replacement for CFBD identity. The importer stores:

- PFF player ID, exported name/team/position, season, and source filename;
- the complete original metric row as JSON;
- dataset-specific primary grade, usage, and game count;
- usage-weighted team/position-group summaries;
- CFBD team IDs and conservative player-link evidence;
- an explainable interest score that discounts samples below eight games and below
  100 dataset-relevant snaps/attempts/routes;
- man/zone coverage and receiving splits, passing depth, return work, and detailed
  run-defense evidence in a separate supplemental table;

Historical `oline_data/ol_*.csv` rows add player-career context. The NFL team-grade
exports are deliberately excluded from the college store to prevent cross-league
contamination.

Linking 2025 PFF players to the 2026 CFBD roster follows three rules:

1. Exact normalized name plus the same canonical team creates a confirmed link.
2. A name unique in the entire roster but on another team is only a
   `possible_transfer` candidate; it does not populate the canonical player link.
3. Duplicate or absent names remain ambiguous/unresolved.

The current import contains 24,459 core metric rows, 69,742 supplemental rows,
and 10,872 unique 2025 players: 5,324 exact same-team links, 1,937 transfer
candidates, and 3,611 unresolved or ambiguous players. All 136 PFF team labels
resolve to current CFBD teams through
exact aliases or reviewed deterministic overrides.

## Entity-matching path for posts

Incoming posts should use the same conservative identity model:

1. Resolve source by DID, never display handle alone as canonical identity.
2. Extract team candidates and query `team_aliases`; one exact canonical candidate
   may attach, multiple candidates require context/review.
3. Extract player names and prefer CFBD player IDs constrained by resolved team and
   season. PFF IDs remain provider IDs, with the crosswalk status retained.
4. Search scheduled games containing the resolved teams. Both teams plus one unique
   upcoming game creates a high-confidence link; a single-team next-game candidate
   remains explicitly labeled `Team context`.
5. Store every entity candidate, confidence, method, and review state. Do not hide
   failed resolution by forcing a match.

## Content ingestion now implemented

`social/content.py` now persists source content with stable platform IDs, source
entity/endpoint attribution, timestamps, links, roles, raw provenance, and separate
topic/team/player/game relationship tables. The web API omits raw payloads.

The first live Bluesky sample on 2026-08-23:

- attempted and completed all 40 verified endpoints;
- received 358 feed records;
- suppressed 62 reposts;
- stored 296 canonical AT-URI keyed posts;
- produced 27 conservative current-roster player links;
- produced three high-confidence game links.

Topic labels are deterministic candidate labels, not extracted facts. Reporter
posts are `REPORTING_UNDETERMINED`, never automatically `ORIGINAL_REPORT`.
Game previews separate reporting, official information, analysis, scouting, and
community layers and label lower-confidence material as team context.

## Reddit and media providers

`r/CFB`, `r/CFBAnalysis`, and `r/NFL_Draft` were live-verified through the configured
Reddit API and stored with stable `t5_` IDs. Reddit link submissions credit the
external domain as publisher and retain the subreddit only as the discovery
endpoint. Self-posts and rumors are not converted into factual reporting.

YouTube uses the official Data API channel, uploads-playlist, playlist-item, and
video resources. Podcast ingestion validates RSS and preserves GUID/audio identity.
The eight named shows from the extension remain `SOURCE_CANDIDATE` rows because no
channel ID or feed was supplied or guessed.

## Reddit ingestion

Subreddit submissions are now ingested, not only validated. Classification runs
flair first, then title structure, then the link target, so a game thread is
never mistaken for reporting. `links_externally` rejects crossposts and image
posts that point back at reddit.com, which would otherwise be credited as
discovery of an outside story.

A submission that links out stores the external domain as `publisher_name`, the
outside URL as `original_url` and `ORIGINAL` link, and the permalink as the
`DISCOVERY` link. Its role is `AGGREGATION`. No Reddit path can produce
`ORIGINAL_REPORT`.

The first live run on 2026-08-23 stored 75 submissions across r/CFB,
r/CFBAnalysis and r/NFL_Draft, crediting eight external publishers.

## Relevance ranking

Content is ordered by a stored, explainable score rather than by publication
time:

```text
reliability x expertise x role x importance x recency x specificity
```

Expertise is selected per topic, so a source that rates 5/5 on breaking news is
not treated as 5/5 on scheme. Recency uses a per-topic half-life. Specificity
rewards items that resolved to a scheduled game, then a rostered player, then a
team. A source whose beat covers the resolved team receives a modest bonus.

Every score persists its factors, and the UI shows them under the headline. The
weights are provisional and expected to change once classification precision has
been measured; they order presentation and never promote an item to a fact.

## Conference tagging

`content_conferences` derives conferences from resolved teams, then from the
source beat. Conference names are deliberately not matched from free text:
"Big Ten" appears in national copy that concerns one specific team.

`retag` re-runs every resolver over stored text, so rule improvements apply to
the archive without re-fetching. The backfill produced 654 conference links and
raised team links from 723 to 813.

## Media validation and ingestion

YouTube channels and podcast feeds are discovered through search, then scored
before promotion. The scorer combines name agreement, audience size, publishing
history, and college-football vocabulary, and records every blocker.

The live run on 2026-08-23 promoted six channels and five feeds and left two
candidates in review. A show that publishes on both platforms becomes one source
entity with two endpoints, so an episode distributed twice cannot double its
apparent authority.

Videos and episodes are classified from published metadata only
(`GAME_PREVIEW`, `PRESS_CONFERENCE`, `FILM_BREAKDOWN`, `DRAFT_ANALYSIS`,
`GAME_REACTION`, `RANKINGS`, `HIGHLIGHTS`, falling back to `VIDEO_ANALYSIS`).
Captions are not fetched, so no classification claims more than the title and
description support.

## Team resolution

Two defects were found by auditing stored links against the teams named in the
text:

1. **Three-letter programmes never resolved.** A four-character alias floor
   discarded USC, LSU, BYU, TCU and their peers before comparison. Short
   abbreviations are now matched against the original text in upper case, where
   "USC" is a deliberate reference and a lowercase run inside a word is not.
2. **Roundups credited every programme equally.** A post ranking all of FBS
   linked 24 teams at reporting-strength confidence and then seeded game links
   from them. Above `LIST_MENTION_THRESHOLD` distinct teams, every mention is
   demoted to `list_mention` at 0.3 and excluded from player, game, and
   conference resolution.

Teams named in the lead now outrank teams buried in the body. After retagging,
team links rose from 1,504 to 1,755, player links from 130 to 250, and no
sampled unlinked item names a team that the resolver missed.

## Context is not reporting

Team and player pages previously merged conference-level stories into the
subject's reporting stream behind a text label. In practice a Boise State page
with no team-linked stories filled entirely with items about other Mountain West
programmes, so an SDSU story read as Boise State reporting.

Context now has its own section, its own visual treatment, and copy that states
plainly that the stories are not about the subject. Conference-wire items exclude
anything already linked to the team, and each carries the team it actually
concerns. The same rule applies on player pages, where team reporting is context
rather than reporting about the player.

## Player resolution

Requiring a player's team to be among the teams resolved from the text dropped
mentions whose team was never named -- a column about Arch Manning that resolves
other programmes still concerns him. A name unique across the entire roster now
links at reduced confidence with the weaker method recorded
(`exact_full_name_unscoped`), while a name on a resolved team keeps full
confidence. Recall against items naming a unique rostered player went from 148 of
161 to 161 of 161.

The resolver now checks the current roster first and falls back one season only
when that name was not matched in the active roster. Prior-season links retain
their true roster season and are capped at lower confidence, which keeps recent
graduates, draftees, and transfers discoverable without allowing stale rows to
override active players. A full name in the headline is recorded as headline
evidence and receives a bounded confidence boost.

## Game resolution

A team mention alone no longer attaches the next scheduled game. Two confidently
resolved opponents can identify a unique meeting; otherwise a one-team item must
contain game-level language. Preview and recap vocabulary constrains the search to
future or recent games, publication time selects the nearest plausible event, and
an explicit week label wins over temporal proximity. Both `Week 0` and `Week Zero`
resolve to week zero. These decisions are stored as distinct match methods for the
link audit.

## RSS source expansion

The national article stream includes ESPN, Yahoo Sports' verified college-football
feed at `https://sports.yahoo.com/college-football/rss/`, and the official NCAA.com
FBS feed at `https://www.ncaa.com/news/football/fbs/rss.xml`. Each publisher has
its own source entity, and NCAA.com is represented as an official primary source
rather than an anonymous feed. New conference or team feeds should follow the
same rule: verify an exact public endpoint, add its entity and beat scope, then
activate it; a guessed URL is never promoted directly.

## Headlines

Video and podcast rows stored the full description as body text, and the game
page rendered it as the headline, producing walls of subscribe links and
hashtags. `display_text` prefers the title, strips URLs, hashtag runs, and
promotional boilerplate from anything else, and falls back to the cleaned text
rather than reporting a titled item as untitled.

## Auditing resolution

`/college-football/admin/links/` lists every team and player link with the words
that produced it, the rule that fired, and the confidence. Weak-rule rows are
marked, because those are the ones worth checking.

Auditing the unscoped player rule immediately found its failure mode: names are
shared across levels of football. The Chiefs' Trey Smith was linking to Purdue's,
and a Jaguars preseason post was linking to a Vanderbilt lineman. An unscoped
match is now blocked when professional-football vocabulary appears anywhere in
the item, and when a coaching title sits beside the name. Links fell from 284 to
268, and the two known-bad links are gone.

A related defect surfaced while building the audit: tag evidence was written
immediately after team resolution, before player resolution had run, so no player
link ever recorded its evidence. Evidence is now written once, after every
resolver has contributed.

## Article matching, third pass

Auditing the stored links surfaced three weaknesses beyond the earlier fixes:

1. **Transfer stories credited both schools equally.** "Ole Miss files suit
   against two players who transferred to LSU" linked both at the same strength.
   Destination phrasing now raises the school a player is joining and demotes the
   one he left to 0.55 as `transfer_origin`.
2. **Headline mentions were not distinguished from body mentions.** A team named
   in the headline is what an item is about; one named deep in the body often is
   not. Headline matches now score up to 0.98 and body-only matches drop by 0.15,
   which spread confidence from a three-value cluster across 0.30 to 0.98.
3. **Topic coverage was 46%.** More than half of stored content matched no topic,
   which left the relevance model nothing to weigh. Nine topics were added
   (betting, schedule, offseason, bowl, season preview, discipline, facilities,
   commentary, media), each with its own weight, half-life and expertise
   dimension. Coverage is now 54%; the remainder are short posts with no subject.

## Team subreddits

46 power-conference subreddits are verified and always-on. Discovery generates
candidates from the school, mascot and abbreviation, then verifies each live and
requires the description to read as football. Two guards proved necessary: a bare
mascot shared with a professional team (r/Eagles is Philadelphia, not Boston
College) and a description that reads as professional football (r/PittsburghPanthers).
Four candidates were dropped on those rules.

The audited mappings are committed in `data/team_subreddits.json`. Preparation,
Reddit validation, and direct Reddit ingestion all promote those mappings into
the unified source graph. This promotion is required: `team_subreddits` drives
poll planning, while `source_endpoints` is what ingestion and the UI query.

Outside the always-on tier, a team is polled when it plays an activated game,
when it sits inside the Elo top 25, or when its Elo has moved 40 points or more
this season. The last rule is the one that reaches G5 teams becoming interesting
before they are obvious, without polling all 138.

## Next implementation sequence

Completed in this pass:

1. Story URLs retain identity-bearing query parameters while dropping tracking
   parameters. Platform permalinks and one-source boilerplate homepages do not
   become shared-story keys.
2. Similarity merging now compares items with different article URLs, requires
   independent sources plus confident shared entities/topics, and prevents a run
   of similar videos from one channel from swallowing the feed.
3. `content_review_labels` plus the `review-export`, `review-import`, and
   `review-report` commands provide the review queue and measured precision/recall
   loop described in [CLASSIFIER_REVIEW.md](CLASSIFIER_REVIEW.md).

The next increments should be:

1. Label the first stratified packet and establish held-out baselines before
   tuning topic, role, relevance, or entity rules.
2. Exercise the untested in-season advanced-statistics, CORE rating, recap,
   situation, and Elo update paths against replayed completed games.
3. Add a scheduler for routine data and source refreshes.
4. Validate official endpoints, then ingest videos, releases, game notes, and
   press conferences into the same content tables.
5. Expand verified beat coverage outside the power four only after classifier and
   clustering quality are measured.

This order keeps the feed useful and auditable while avoiding premature automated
trust, false player transfers, and a rewrite of working legacy functionality.
