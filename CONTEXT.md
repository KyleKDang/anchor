# Anchor

A personal movie taste-engine: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns the owner's taste.

## Language

### Accounts

**Account**:
A registered user of Anchor with fully separate data: ratings, ordering, anchors, watchlist, and taste profile.
Accounts do not interact with each other.

**Owner**:
The account whose data is in question.
Every rating, comparison, ordering, and watchlist belongs to exactly one owner.

### Rating

**Film**:
A single movie, identified by its TMDB entry.

**Comparison**:
A single pairwise judgment by the owner: one film ranks above the other, or the two are tied.
Only overall comparisons move the ordering.

**Ordering**:
The sequence of tie-groups over all rated films produced by comparisons.
The durable, drift-proof layer of the rating system: nothing moves except through the owner's judgments or explicit actions.
_Avoid_: shelf, ranking, total order

**Tie-group**:
A set of films occupying a single slot in the ordering: judged equal by the owner (definitive) or seeded equal by the import (provisional).
A provisional tie-group is a placeholder, not a judgment; it dissolves as comparisons pull films out.

**Anchor**:
A film the owner designates as the canonical exemplar of a band (the definitive 4.0).
At most one per band; comparisons cannot move anchors, only the owner can, and an anchor re-placed outside its band is automatically retired.

**Divider**:
The derived boundary between two adjacent bands, sitting between their anchors in the ordering.
Pinned by the owner's band judgments (imported seed ratings count, at lower weight than live answers) and movable as those judgments accumulate; never set directly.

**Band**:
A half-star rating bucket (e.g. 3.5), centered on its anchor and bounded by the dividers on either side.
A band with no anchor still works fully; its dividers persist independently.

**Rating**:
A film's half-star value, derived from which dividers its position sits between.
Never entered directly.
A film placed before any band structure exists shows its position only; its rating materializes as anchors and band judgments accumulate.
_Avoid_: score (ambiguous with recommender scoring)

**Placement**:
The comparison flow that finds a new film's slot in the ordering: seeded by an optional ballpark guess, narrowed against anchors until the band locks, then bisected within the band.

**Ballpark guess**:
An optional half-star estimate the owner gives when logging a film.
Seeds the placement search at the nearest anchor but never sets the rating.

**Sliver question**:
The band-assignment question asked only when a film lands between the highest known film of one band and the lowest known film of the next: closer in quality to which of the two canonical films?

**Provisional placement**:
A film's position trusted less than a fully-compared one: produced by the seed import or by ending a placement early once the band is locked.
Refined by later comparisons; graduates to fully trusted when the advisory math's confidence crosses the same threshold a normal placement needs.

**Comparison log**:
The append-only record of every judgment: comparisons, skips, sliver answers, and criteria answers, each with its context.
Entries are active, in tension (contradicting the ordering), or superseded (settled against by an owner resolution); never deleted (an account reset or deletion is the only exception).

**Criteria question**:
A comparison on a single quality of two films (screenplay, acting, shots).
Feeds only the taste profile; never moves the ordering.

**Drift**:
The condition where later judgments contradict a film's position in the ordering.
Detected by the app, never auto-corrected; the owner resolves it through the film's drift flag.

**Drift flag**:
The per-film aggregation of the in-tension judgments implicating a film; at most one open flag per film.
Closes when the owner resolves it (re-place, keep, or re-point at the opponent film) or when all its evidence resolves on its own.

**Drift check**:
A targeted comparison the app slips into a normal comparison moment to confirm or clear a suspected drift before surfacing the flag.

**Re-placement**:
The placement flow run again for an already-rated film, entered from drift resolution, a rewatch, or an anchor-designation mismatch.
Head-started by the film's in-tension judgments where they exist; its outcome always wins over the owner's stated intent.

**Rewatch**:
A repeat watch of a rated film, timestamped internally.
Offers an optional re-placement but never forces one; keeping the current position is a confirming signal.

**Seed import**:
The one-time import of an owner's Letterboxd CSV export that bootstraps their ordering, backlog, and watch history.
Anchor has no live Letterboxd connection; this is the only data that crosses over.
Importing over existing account data, seeded or organic, is allowed only as a hard reset: behind an explicit warning that enumerates what will be destroyed, it wipes all account data and rebuilds from the new export alone.

**Unmatched row**:
An import row with no bound TMDB film (TV-side entries, deleted films, failed searches).
Affects nothing until the owner binds a film manually or dismisses it; the unmatched list stays open indefinitely.

**Watched-unrated film**:
A film imported as watched but never rated: outside the ordering, the backlog, and the taste profile.
Its only effects are a discovery-feed dedupe (never recommend a seen film) and an optional later invitation to place it.

**Warmup**:
The skippable guided sequence offered when an account starts: designate anchors, gather first comparison evidence, seed the backlog.
One skeleton with two fills, post-import (import-ranked anchor candidates, advisory comparisons, watchlist rows) and fresh (search-driven designation, first placements, hand-added films); the app is fully usable the moment any part is skipped.

### Watchlist

**Backlog**:
The unlimited tier of the watchlist: every unwatched film the owner has added.

**Ranked tier**:
The small ordered tier of the watchlist (~25-50 films): what to watch next.
Generated and continuously maintained by the recommendation engine; draws only from the backlog. Manual overrides (pin, veto, force a promotion) are exceptions, not the workflow.

**Taste profile**:
What the recommendation engine has learned about the owner's preferences from ratings (and possibly richer signals). Owns the ranked tier of the watchlist.

### Discovery

**Discovery feed**:
Recommendations of films the owner has never added: suggestions from the wider catalog to consider adding to the backlog.
Kept separate from the ranked tier, which never promotes a film the owner didn't backlog.

### Data sources

**No-training provider rule**:
The compliance gate for AI providers: TMDB content and the taste profile may be sent only to APIs whose terms bar training on customer inputs by default.
See ADR 0003 for the full licensing posture, bright lines, and fallback.
