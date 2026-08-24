# Classifier review workflow

The content pipeline now has a human-labelled evaluation loop. Its purpose is to
measure the rules before changing topic weights, source-role decisions, entity
confidence thresholds, or feed relevance weights.

## 1. Export an exception-focused packet

```powershell
python -m sports_aggregator.social.content_cli review-export `
  --limit 50 --review-mode triage --reviewer editorial `
  --output instance/cfb_content_review.csv
```

Triage mode selects uncertain roles, ranking-boundary items, missing topic/entity
scope, borderline entity links, and relevance/entity disagreements. It then
round-robins across platform, predicted role, and topic so one prolific feed cannot
consume the packet. The `review_reason` column explains why each row was selected.
Items already labelled by the same reviewer are excluded from the next export.

After several triage passes, run a small blind audit to detect confident mistakes
that exception rules cannot see:

```powershell
python -m sports_aggregator.social.content_cli review-export `
  --limit 25 --review-mode stratified --reviewer editorial-audit `
  --output instance/cfb_content_audit.csv
```

This two-lane process concentrates routine effort where a correction can change
the system while preserving an unbiased measurement sample.

Do not edit the prediction columns. Complete whichever label dimensions can be
judged confidently:

- `label_relevant`: `1` for CFB-relevant, `0` otherwise.
- `label_topics`: pipe-separated topic names, or `NONE` for no applicable topic.
- `label_role`: the correct machine role, such as `REPORTING`, `ANALYSIS`,
  `AGGREGATION`, or `COMMUNITY_REACTION`.
- `label_team_ids`: pipe-separated canonical CFBD team IDs, or `NONE`.
- `label_player_keys`: `season:player_id` values separated by pipes, or `NONE`.
- `label_priority`: editorial value from 1 (lowest) to 5 (highest).
- `notes`: optional explanation of ambiguous or incorrect predictions.

A blank cell means “not reviewed” and is excluded from that dimension's metrics.
`NONE` means the field was reviewed and the correct label is an empty set. This
distinction prevents skipped work from being counted as a true negative.

## 2. Import labels

```powershell
python -m sports_aggregator.social.content_cli review-import `
  --input instance/cfb_content_review.csv --reviewer editorial
```

Labels are stored in `content_review_labels` by content item and reviewer. A later
import from the same reviewer updates the existing label without changing the
source content or classifier output.

## 3. Measure current rules

```powershell
python -m sports_aggregator.social.content_cli review-report --reviewer editorial
```

The JSON report includes:

- relevance accuracy, precision, recall, and F1;
- topic micro precision/recall/F1 and exact-set agreement;
- source-role accuracy and a confusion map;
- team precision/recall at confidence thresholds 0.75 and 0.90;
- player precision/recall at confidence 0.75;
- Spearman rank correlation between relevance score and human priority.

The report always evaluates the saved labels against the current classifier. Run
it before and after a rule change to see whether the change improved held-out
examples rather than only the examples that motivated it.

## Evaluation discipline

Keep a portion of reviewed items as a holdout before tuning. Do not promote a rule
solely because it improves aggregate accuracy: CFB relevance is imbalanced, so
precision and recall by dimension matter more. For entity links used in factual
summaries, prefer high precision even when recall is lower.

## Article classification rubric

Classify the item before deciding whether it belongs to an existing story. Use
this order so that format, subject, and story identity do not get conflated:

1. **Relevance:** Does the item materially concern college football? A passing
   school mention, generic promotion, or unrelated multi-sport post is not enough.
2. **Source role:** Label what the item is doing: original reporting, analysis,
   aggregation, official information, or community reaction. Role is independent
   of whether the claim later proves correct.
3. **Topics:** Apply every supported topic, not just the dominant one. Use only
   evidence in the headline and body; do not infer recruiting, injury, transfer,
   or discipline labels from a player's name alone.
4. **Entities:** Attach teams and players only when the text identifies them with
   enough context to avoid namesakes. Conference-wide roundups can remain at
   conference scope instead of forcing team links.
5. **Priority:** Score editorial usefulness from 1 to 5. Original, specific,
   timely reporting with clear entities should outrank recycled or vague posts.
6. **Story cluster:** After item labels are set, group corroborating coverage of
   the same underlying event. Similar subject matter is not sufficient; two
   transfer stories about different players are two stories.

Examples:

- A beat reporter breaking a starting-quarterback injury is relevant,
  `REPORTING`, tagged `INJURY`, linked to the team and player, and normally high
  priority.
- A film review of a receiver's route tree is relevant, `ANALYSIS`, linked to the
  player/team, but it should not join a later injury cluster merely because the
  same player appears.
- An official schedule graphic is relevant official information, tagged
  `SCHEDULE`; it is not original reporting and generally receives a lower
  editorial priority than a material roster development.
