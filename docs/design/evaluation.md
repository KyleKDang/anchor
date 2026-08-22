# Recommender quality evaluation

Consolidates wayfinder ticket [Recommender quality evaluation (#19)](https://github.com/KyleKDang/anchor/issues/19), recorded as [ADR 0012](../adr/0012-evaluation-reads-but-never-feeds.md).

## Framing

The feeding constraints from [watchlist.md](watchlist.md) and [discovery.md](discovery.md) bind what may teach the taste profile, not what may be counted.
Evaluation reads every recorded event - vetoes, not-nows, rotations, accepts, dismissals, seen-its, watches, placements - but nothing it computes ever flows back into the profile, the ordering, or any engine decision.
Measurement and learning are separate consumers of the same records.

Evaluation serves the operator and the build process only.
No owner-facing quality surface ships in v1: it would be dishonest at this data volume and borders on engine narration ([ADR 0011](../adr/0011-no-nagging-surfacing-policy.md)); the prose profile is already the owner's window into the engine.

## Ground truth: the landing

Anchor has a signal most recommenders never get: when the owner watches an engine pick, they place it, and the placement is a real taste judgment.

- "The ranked tier is working" means: films the engine put in the tier, once watched, land higher in the ordering than the owner's same-window hand-picked watches.
  Each watched pick records its landing percentile; the headline claim is always that comparison, never a fixed target.
- "The discovery feed is working" has the same shape: landings of accepted-then-watched films versus hand-added films, plus healthy accept and dismissal rates.
- A rate-later placement completes the fact whenever it happens; the measure reads the ordering at computation time.

This signal judges the engine over months, which is acceptable for a personal tool and is why the fast metric exists.

## Fast metric: held-out pairwise accuracy

At each weight-vector retrain, the worker holds out a slice of the owner's explicit comparisons, trains on the rest, and appends one per-account metrics row (accuracy plus the evidence counts that contextualize it).
It catches a broken or degrading scorer immediately and doubles as a regression check when the feature set changes.
The LLM discovery layer gets no offline metric in v1 - no ground truth exists for verdicts until watches happen - so it leans on landings plus dismissal and ignore rates.

## Attribution: stamp at watch time

Tier membership churns at session boundaries and no tier history is kept ([ADR 0010](../adr/0010-comparison-log-is-evidence-not-event-source.md)), so provenance is stamped at watch time, never reconstructed.
Each watch event records the film's standing when logged (up-next, pool, pinned, or plain backlog) and its origin (discovery accept, hand-added, import-seeded).
A pinned film counts as the owner's pick, never the engine's.
The stamp is capture-or-lose-forever, so it ships with the first version that logs watches.

## The named indicator set

Everything else stays queryable but unnamed; these are the indicators with names, all denominated in opportunities, never calendar time:

- **Tier adoption**: tier-sourced share of logged watches.
- **Rotation rate**: staleness demotions per N watches.
- **Accept rate** and **dismissal rate**: per restock.
- **The discovery funnel**: accepted → watched → placed.

## Where the numbers live

- The worker-written append-only metrics table holds the training-time numbers.
- Every behavioral metric is derived by documented SQL queries versioned in the repo.
- No admin UI in v1 (an easy later add - the data model is already right), no targets, no alerting anywhere; directional human reading is the only judgment form.
- The demo account is excluded from every aggregate.

## The v2 clause: closed

Evaluation never needed richer feedback - it needed attribution, which the stamp provides.
If the slow signal proves too sparse, the remedy is patience, not a new channel.
Any future proposal to let behavior teach the profile is a taste-model change that reopens the watchlist and discovery feeding decisions and [ADR 0006](../adr/0006-discovery-dismissals-feed-the-profile.md) - a fresh effort beyond this design.
