# The watchlist

Consolidates wayfinder ticket [Ranked tier maintenance policy (#8)](https://github.com/KyleKDang/anchor/issues/8).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md).

The watchlist has two tiers: the unlimited backlog (every unwatched film the owner has added) and the ranked tier the engine maintains on top of it.

## Shape

- **Fixed cap of 30**: the tier is the top-30 of the backlog by weight-vector score; a backlog at or under the cap shows fully ranked.
- **Two zones**: the up-next zone (top 5, strictly ordered, a real "watch these next" statement) and the pool (remaining 25, loosely ordered, order floats freely).
  A tier smaller than 5 is all up-next.
- The tier draws only from the backlog.
  This was deliberately reconsidered and reaffirmed: discovery stays quarantined in its own feed, with the fast path that an accepted suggestion, once backlogged, may enter the tier immediately under the newly-backlogged exception below.

## The honesty rule

Nothing rating-shaped is ever shown for an unwatched film - no predicted band or stars, not even on-demand ([ADR 0005](../adr/0005-no-rating-shaped-predictions.md)).
A prediction seen before watching can tilt the comparison answers themselves, and that contamination is invisible to drift detection and permanent in the ordering.
Position is the entire public statement; any "why is this here" surface speaks in non-rating vocabulary (exemplar-based explanations).
The rule binds the ranked tier, the backlog, the discovery feed, and search.

## Refresh and churn damping

- Scores recompute on every ordering change (free, per [ADR 0004](../adr/0004-two-scorer-taste-architecture.md)); the visible list changes only at session boundaries - next app open or end of a rating session - never live under the cursor.
- Damping mechanisms are spec, their numbers implementation-tunable: entry/exit hysteresis (small score wobbles never swap membership), a per-refresh swap budget (big profile shifts roll in over days), and enter/exit cooldowns (no immediate drops, no bounce-backs).
- Two exceptions to the swap budget: a newly backlogged film that scores in enters immediately (the owner told the app something; reacting is the point), and vacancy refills (watched, vetoed, removed) bypass it - refilling a seat is not churn.
- No staged next tier exists: a session boundary is a moment, not a record; maintenance runs then against the one persisted tier state (see [data-model.md](data-model.md)).

## Staleness and rotation

Staleness is measured in owner activity, never calendar time: a tier film repeatedly passed over (indicatively ~10 logged watches without picking it, tunable) rotates out with a re-entry cooldown.
Its score is untouched, so a strong film returns after the cooldown.
Every cooldown is denominated in the watch clock, so a dormant account never shuffles itself.

## Overrides

Three overrides, all immediate (never queued behind the swap budget), all pure queue management:

- **Pin**: works on any backlog film; puts or holds it in the up-next zone above the engine's picks, ordered by pin time, immune to all automatic maintenance, capped at the zone size (5).
  A pinned film leaves only by watch, unpin, or backlog removal.
  "Force a promotion" is not a separate concept; it collapses into pin.
- **Veto**: permanently bars a film from the tier until lifted; the film stays in the backlog with its score untouched, reversible from a visible vetoed list.
  Saving a film for an occasion is a veto use-case, so it must never read as distaste.
- **Not-now**: immediate rotation with the standard cooldown - the temporary, mood-level version of a veto.

## The profile firewall

None of the tier's passive or override signals - lingering, rotation, not-now, veto, pin - feed the taste profile in v1.
Only the ordering trains taste (ADR 0004).
Any future proposal to let tier behavior teach the profile is a taste-model change requiring a fresh decision (see [evaluation.md](evaluation.md) and [ADR 0012](../adr/0012-evaluation-reads-but-never-feeds.md)).

## Lifecycle edges

- The tier exists only at taste-profile readiness *ready* (defined in [taste-profile.md](taste-profile.md)); the pre-gate state is the honestly-unranked backlog fixed in [onboarding-and-import.md](onboarding-and-import.md).
- The only way back below *ready* is the hard-reset re-import, which wipes overrides with everything else and re-locks the tier until evidence re-accumulates.
- Tier changes land silently; surfacing placements are fixed in [surfacing.md](surfacing.md).
