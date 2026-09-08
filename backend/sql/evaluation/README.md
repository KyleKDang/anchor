# Evaluation queries

The operator's read on whether the recommender is working.
These are the documented, versioned queries [evaluation.md](../../../docs/design/evaluation.md) calls for, and they cover the whole named indicator set.

They are operator artifacts and nothing else.
Nothing in `src/` loads them, and nothing they compute reaches the taste profile, the ordering, or any engine decision: evaluation reads every recorded event and feeds none of them back ([ADR 0012](../../../docs/adr/0012-evaluation-reads-but-never-feeds.md)).
That firewall is structural rather than a promise - there is no code path by which a result could be read back into the app - and `backend/tests/test_evaluation.py` asserts it stays that way.

## Running them

```sh
psql "$DATABASE_URL" -f backend/sql/evaluation/tier_adoption.sql
```

`landings.sql` takes one parameter, the number of watches a comparison window holds:

```sh
psql "$DATABASE_URL" -v watches_per_window=25 \
  -f backend/sql/evaluation/landings.sql
```

Every query returns one row per account (`landings.sql`, one row per account per window) and excludes the demo account.

## The indicators

| Query | Indicator | Denominated in |
| --- | --- | --- |
| `tier_adoption.sql` | Tier-sourced share of logged watches | watches |
| `rotation_rate.sql` | Staleness demotions per watch | watches |
| `discovery_rates.sql` | Accept rate and dismissal rate | restocks |
| `discovery_funnel.sql` | Accepted, then watched, then rated | accepts |
| `landings.sql` | Engine picks against same-window hand-picked watches | watches, per window |

Everything else about the database stays queryable but unnamed.
The fast metric - held-out pairwise accuracy - is not here: the worker writes it to `taste_metrics` at each retrain, one appended row per retrain, and that table is the whole of it.

## How to read them

**Denominated in opportunities, never calendar time.**
Every rate here is per watch, per restock, or per accept.
That is not a stylistic choice: the tier's staleness, the feed's rotation and every cooldown in Anchor are counted the same way, so an owner who does nothing for two months has had nothing offered to them and their indicators should not move.
No query in this directory reaches for a date.

**No targets, no thresholds, no alerting.**
Directional human reading is the only judgment form there is.
With a handful of watches a month, any threshold would be noise-tripped, and the app's posture is to surface evidence and never to auto-judge.
A number that looks wrong is a question, not a verdict.

**The comparison is the claim, never the level.**
`landings.sql` is the one that matters, and it is the only one that comes with its own control: the owner's same-window hand-picked watches.
"Engine picks land at 0.71" means nothing on its own; "engine picks land at 0.71 where the owner's own land at 0.55, over sixty watches" is the claim.
Read `engine_unplaced` and `owner_unplaced` beside it - those are the watches whose fact is still open.

**The slow signal is slow.**
Landings judge the engine over months, which is acceptable for a personal tool.
If it looks too sparse to read, the remedy is patience rather than a new feedback channel.

## Two counters, and why they exist

Two indicators are denominated in opportunities that left no countable trace, so the opportunity is counted where it happens.

- `tier_states.staleness_rotations` - a rotation writes a re-entry mark that the next one overwrites, that a displacement and a not-now write identically, and that is cleared outright when the film takes a seat again. By the time anybody asks, the rotations are gone.
- `feed_states.restock_counter` - the row stamps the profile version the last restock ran for, which answers whether another is due and keeps no history at all.

Both were added at zero for existing rows rather than backfilled from a guess.
Both are written by one code path each and read by these queries alone; nothing in the engine's rules may branch on either.
