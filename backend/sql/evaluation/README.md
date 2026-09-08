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

Every query excludes the demo account.
All but one return a row for every account, whether or not it has any activity yet; `landings.sql` returns one row per account per window of watches, and so nothing at all for an account that has not watched anything.

## The indicators

| Query | Indicator | Denominated in |
| --- | --- | --- |
| `tier_adoption.sql` | Tier-sourced share of logged watches | watches |
| `rotation_rate.sql` | Staleness demotions per watch | watches |
| `discovery_rates.sql` | Accept rate and dismissal rate | restocks |
| `discovery_funnel.sql` | Accepted, then watched, then rated | accepts |
| `landings.sql` | Engine picks against same-window hand-picked watches, and accepted films against hand-added ones | watches, per window |

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
`landings.sql` is the one that matters, and it is the only one that comes with its own control.
It makes the same comparison twice, on the two halves of the provenance the watch event stamps: the tier's picks (`engine_*`) against the owner's own (`owner_*`), and the feed's accepted films (`discovery_*`) against hand-added ones (`hand_added_*`).
The two axes read the same watches from different sides, so an accepted film that took a tier seat before it was watched honestly counts in both.
"Engine picks land at 0.71" means nothing on its own; "engine picks land at 0.71 where the owner's own land at 0.55, over sixty watches" is the claim.
Read the `*_unplaced` columns beside it - those are the watches whose fact is still open.

**The slow signal is slow.**
Landings judge the engine over months, which is acceptable for a personal tool.
If it looks too sparse to read, the remedy is patience rather than a new feedback channel.

## The counters, and why they exist

Some of these indicators are denominated in opportunities that left no countable trace, so the opportunity is counted where it happens.

- `tier_states.staleness_rotations` - a rotation writes a re-entry mark that the next one overwrites, that a displacement and a not-now write identically, and that is cleared outright when the film takes a seat again.
  By the time anybody asks, the rotations are gone.
- `feed_states.restock_counter` - the row stamps the profile version the last restock ran for, which answers whether another is due and keeps no history at all.
- `feed_states.accept_counter` and `feed_states.dismissal_counter` - an accept writes an account-film that removing the film from the backlog deletes outright, so counting the rows would quietly drop exactly the accepts the owner thought better of.
  The dismissal rows are durable, but they run from the account's first day while the restock counter runs from the day it was added, and a rate whose two sides cover different spans is not a rate.

A counter that starts mid-life has to say where it started, or the rate built on it divides a numerator that begins today by a denominator running back to the account's first week.
So `tier_states.rotations_counted_from` records the watch clock the tier's counter began at, and the feed's three all start together.
Nothing is backfilled from a guess: the restocks and accepts that already happened cannot be recovered.

Each is written by one code path and read by these queries alone; nothing in the engine's rules may branch on any of them.

## What these queries cannot see

Said plainly, because an indicator whose blind spots are undocumented is worse than no indicator.

- A diary row from a seed import is a real watch event and moves the watch clock, but it is nobody's pick: it carries its own date and sits before the account existed.
  `tier_adoption.sql`, `rotation_rate.sql` and `landings.sql` set those aside, and the first two report how many they set aside.
  A diary row that arrived without a date is stamped on import and so counts as an ordinary watch - a small residue, and the only one.
- An accepted film the owner later removed from the backlog leaves `discovery_funnel.sql` entirely rather than sitting in it as an accept that went nowhere, because the funnel reads the film's lifecycle state and a removed film has no row.
  `discovery_rates.sql` counts it, so the gap between `accepted` there and `accepts` here is exactly the films that were taken back.
- Nothing before the counters existed is recoverable.
