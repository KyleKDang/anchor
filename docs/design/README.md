# Anchor design spec

Anchor is a personal movie taste-engine web app: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns each account owner's taste.
It complements Letterboxd but is not connected to it; a one-time seed import is the only crossover.

This directory is the complete design spec, assembled by wayfinder ticket [Spec assembly (#14)](https://github.com/KyleKDang/anchor/issues/14) from the resolutions of the [Anchor design map (#1)](https://github.com/KyleKDang/anchor/issues/1) (charted 2026-07-26, completed 2026-08-22).
It stands on its own: implementation planning should be possible from these docs without reopening the design tickets.
The tickets and their resolution comments remain the provenance trail; each doc names the tickets it consolidates.

Visual and UI design was deliberately excluded from the original spec: it fixes screens, states, and behavior, and left implementation to prototype UI directions rather than pre-picking one.
That deferral came due at [#50](https://github.com/KyleKDang/anchor/issues/50), and the direction it settled on is recorded in [visual-design.md](visual-design.md).

## How to read this

Read [CONTEXT.md](../../CONTEXT.md) first: it is the ubiquitous language, and every doc here uses its terms without redefining them.
Rationale lives in the [ADRs](../adr/); the spec docs state what the design is and cite the ADR that argues why.
The [research notes](../research/) are the sourced groundwork behind the recommender and data-source decisions.

## Principles

These rules bind every feature, current and future.

1. **Nothing moves behind the owner's back.**
   The ordering is explicit persisted state; the probabilistic machinery is advisory-only and can never reorder it ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md)).
   Every cooldown and staleness measure is denominated in the owner's activity, never calendar time, so a dormant account changes nothing at all.
2. **Ratings are derived, never entered.**
   A film's rating is which dividers its position sits between; placement finds the position through comparisons ([ADR 0002](../adr/0002-anchors-are-centroids-with-derived-dividers.md)).
3. **The rating distribution is emergent.**
   It is never forced or normalized to a curve.
4. **Drift is flagged, never auto-corrected.**
   When later judgments contradict a film's position, the owner resolves; the app only surfaces.
5. **No rating-shaped predictions on unwatched films**, anywhere, in any form ([ADR 0005](../adr/0005-no-rating-shaped-predictions.md)).
6. **Queue actions carry no taste meaning.**
   Pin, veto, not-now, rotation, and discovery accepts feed nothing; the single exception is discovery dismissals as prose-pattern evidence ([ADR 0006](../adr/0006-discovery-dismissals-feed-the-profile.md)).
7. **The overall ordering is Anchor's only ranking structure.**
   Criteria answers stay loose evidence and never build per-quality rankings ([ADR 0007](../adr/0007-criteria-answers-are-evidence-not-orderings.md)).
8. **LLMs are precompute-only**, with spend earned by engagement and capped per account and platform-wide ([ADR 0004](../adr/0004-two-scorer-taste-architecture.md)).
9. **No nagging.**
   In-app only, nothing interrupts, the engine never narrates its background work ([ADR 0011](../adr/0011-no-nagging-surfacing-policy.md)).
10. **Evaluation reads behavior but never teaches the taste profile with it** ([ADR 0012](../adr/0012-evaluation-reads-but-never-feeds.md)).

## The doc set

In suggested reading order:

| Doc | Covers |
| --- | --- |
| [rating-system.md](rating-system.md) | The ordering, anchors, bands, and dividers; the placement flow; the comparison log; drift, re-rating, and rewatches |
| [onboarding-and-import.md](onboarding-and-import.md) | The entry fork, the Letterboxd seed import, fresh-account bootstrap, the warmup, provisional placements and settling, feature gates |
| [taste-profile.md](taste-profile.md) | The three profile artifacts, training-pair extraction, readiness states, the quality system and criteria questions, LLM guardrails |
| [watchlist.md](watchlist.md) | The backlog and the ranked tier: zones, refresh damping, staleness, pin, veto, not-now |
| [discovery.md](discovery.md) | The discovery feed: sourcing cascade, verdicts, accept, dismissal, seen-it, degraded states |
| [screens-and-flows.md](screens-and-flows.md) | Every screen and flow in prose: the five destinations, the film page, placement on screen, logging watches |
| [visual-design.md](visual-design.md) | The visual direction and the rules that hold it together: the one-amber rule, type, the wall-versus-rows rule, the theme, the accessibility floor |
| [surfacing.md](surfacing.md) | The no-nagging posture applied: where every surfacing moment lives, and the Letterboxd sync list |
| [data-model.md](data-model.md) | The conceptual data model: realms, entities, relationships, invariants |
| [architecture.md](architecture.md) | Hosting, stack, processes and jobs, accounts and auth, the LLM seam and spend controls, TMDB integration and compliance |
| [testing.md](testing.md) | The test seams, the fakes, and the test-quality bar for implementation |
| [evaluation.md](evaluation.md) | Recommender quality evaluation: landings, held-out accuracy, the indicator set |
| [demo-account.md](demo-account.md) | The shared read-only demo account: fixture, build, and read-only enforcement |

## Out of scope

Ruled beyond this design's destination; each returns only as a fresh effort, never a resumption.

- Social interaction between accounts (accounts exist; interaction does not).
- Live Letterboxd integration or write-back; only the one-time seed import crosses over.
- Streaming availability info (a plain search answers it).
- Per-film notes or reviews (Letterboxd owns that habit).
- Watch-date diary UI (Anchor timestamps watches internally, but no diary feature).
- TV shows.
- Visual/UI design and styling, deferred to implementation as above.
