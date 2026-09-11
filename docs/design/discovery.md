# The discovery feed

Consolidates wayfinder ticket [Discovery feed design (#9)](https://github.com/KyleKDang/anchor/issues/9), built on the cascade fixed by [taste-profile.md](taste-profile.md) / [ADR 0004](../adr/0004-two-scorer-taste-architecture.md).
Vocabulary follows [CONTEXT.md](../../CONTEXT.md).

The feed recommends films from the wider catalog - films the owner has never added - for the backlog.
It is a first-class feature, not an extra, and it lights up at taste-profile readiness *forming*.

## The shelf

A persistent shelf of ~20 films: a flat list (no themed rows), ordered by the LLM's listwise rerank with linear-scorer tie-breaking.
Position is the entire public statement ([ADR 0005](../adr/0005-no-rating-shaped-predictions.md)): no fit badges or labels of any kind.
Each card shows poster, title, year, director, and genres, its precomputed exemplar-grounded explanation visible by default as the pitch ("Because you loved X and Y - ..."), and the TMDB plot summary behind the standard spoiler toggle.

## Sourcing

Candidate pools (TMDB `/discover` slices steered by top weight-vector features, plus `/similar` and `/recommendations` seeded from the exemplar set) union to a few hundred candidates; the linear scorer prefilters to a shortlist of ~60; the LLM reranks in windows; the top ~20 fill the shelf.
Anything with a cached verdict at the current profile version skips the LLM.

- **A quality gate before any taste**: a film may reach the shelf only if it is credible (~200 TMDB votes), good (a TMDB average of ~6.5, read only once the vote floor holds), a feature (~40 minutes, an unknown runtime failing), and released (dated, and out).
  It is one rule enforced at every stage, because a filter in one place is a filter some source goes round: `/discover` slices ask TMDB for it directly, every sourced row meets it on the way into the union whatever returned it (so the union's cap counts only films the feed could suggest), runtime is checked on the bundled shortlist before any LLM call, and the shelf filters cached verdicts by it so a film that fails leaves at the next session boundary without being re-judged.
  It is a floor, not a taste lever, and it is never shown on a card ([ADR 0005](../adr/0005-no-rating-shaped-predictions.md)).
  How mainstream the owner likes things is left to the linear scorer's popularity prior and the dismissal flow, with no fixed tilt either way, and under the never-pad rule a thin pipeline runs the shelf short rather than relaxing the gate.
- **No feed-specific filter UI**: profile constraints are the one exclusion lever, and constraints with a structural footprint (genre, language) are enforced mechanically in the prefilter, not just in prose.

## Verdicts

Cached per (film, profile version): a coarse fit bucket (strong-fit / plausible / poor-fit), a short exemplar-grounded explanation, and listwise rank context.

- Buckets stay internal; only the explanation is ever shown.
- Poor-fits cache as negatives, never shown and never re-sent to the LLM.
- **Never-pad rule**: the shelf shows only strong-fit and plausible films and simply runs short when the pipeline is thin - no padding to 20, and no re-gating of the *forming* readiness bar.

## Refresh and repetition

- Engine-driven shelf changes land at session boundaries only.
- Owner actions remove a film instantly, and the slot backfills instantly from the next-ranked already-cached shortlist candidate (no LLM call).
- **One spend trigger, the owner's visit.**
  Restocks happen lazily and only when the owner has visited the feed since the last one, so an owner who ignores discovery costs nothing - and that is an absolute, true because the visit gate is asked by every caller rather than by one of them.
- A profile-version bump *schedules* a restock; it does not earn one.
  The restock it queues asks the same visit question as any other and declines when nobody has been back, so a bump on its own costs a queue row and a query rather than a rerank.
  What the bump is for is resumption: a run an outside service cut short stamped nothing, so it stays due and the next bump finishes it without waiting for the owner to arrive again - as does the first restock for an account that has visited but never completed one.
- The cost of that reading is one arrival of staleness, and it is accepted.
  The shelf an owner sees on the arrival right after their taste moves is built on the previous version's verdicts, and the restock that arrival earns lands before the next one.
  That is the same rhythm as every other restock, the first one included, and stale-version verdicts stay usable ordered by the linear scorer (below).
- A suggestion passed over ~3 refreshes without action rotates out with a re-entry cooldown, measured in refreshes survived, never calendar time; its verdict cache is untouched, so return is free.

## Actions

- **Accept** adds the film to the backlog and feeds nothing: anticipation is not judgment, and the signal arrives at full fidelity later through watching and placement.
- **Dismissal** ("not interested") suppresses the film permanently-until-lifted, kept on a reviewable dismissed list.
  Accumulated dismissals feed prose-profile regeneration as pattern evidence only, under a magnitude guard - the one queue signal anywhere in Anchor that feeds the profile ([ADR 0006](../adr/0006-discovery-dismissals-feed-the-profile.md)).
  A single dismissal means nothing; only patterns across many surface in the profile, and the owner's profile constraints override any pattern durably.
- **Seen-it** converts the suggestion to a watched-unrated film (permanent dedupe, rate-later invite) with an optional, skippable "place it now?".
  Splitting seen-it from dismissal is what keeps the dismissal signal clean: a dismissal reliably means the pitch does not appeal.

## Quarantine

Accept lands in the backlog, full stop; the feed never writes to the ranked tier.
Quarantine means no bypass of the engine, not artificial delay: under the newly-backlogged exemption in [watchlist.md](watchlist.md), a strong accept can legitimately reach the tier by the next session, through the scorer, on the same terms as any hand-added film.
No discovery-origin state exists anywhere (the watch-event source stamp in [evaluation.md](evaluation.md) records origin for measurement only).

## Degraded state

One rule keeps every state coherent: a film with no verdict never reaches the shelf.
Under a spend cap, current-version verdicts rank normally, stale-version verdicts stay usable ordered by the linear scorer, and unverdicted films wait.
The shelf may run short; there is no degraded-mode banner, because the feed never shows anything it cannot stand behind.

## Surfacing

The feed is a pure pull surface - it never notifies, badges, or interrupts on its own.
Its complete moment inventory, placed in [surfacing.md](surfacing.md): the discovery-unlock moment at readiness *forming*, the optional fresh-suggestions-since-last-visit marker, and the inline seen-it "place it now?" invite.
Nothing else, ever.
