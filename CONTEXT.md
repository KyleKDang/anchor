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
A set of films the owner has judged equal, occupying a single slot in the ordering.

**Anchor**:
A film the owner designates as the canonical exemplar of a band (the definitive 4.0).
At most one per band; comparisons cannot move anchors, only the owner can, and an anchor re-placed outside its band is automatically retired.

**Divider**:
The derived boundary between two adjacent bands, sitting between their anchors in the ordering.
Pinned by the owner's band judgments and movable as those judgments accumulate; never set directly.

**Band**:
A half-star rating bucket (e.g. 3.5), centered on its anchor and bounded by the dividers on either side.
A band with no anchor still works fully; its dividers persist independently.

**Rating**:
A film's half-star value, derived from which dividers its position sits between.
Never entered directly.
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
Refined by later comparisons.

**Comparison log**:
The append-only record of every judgment: comparisons, skips, sliver answers, and criteria answers, each with its context.
Entries are active, in tension (contradicting the ordering), or superseded (settled against by an owner resolution); never deleted.

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
The one-time import of an owner's Letterboxd CSV export that bootstraps their ordering.
Anchor has no live Letterboxd connection; this is the only data that crosses over.

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
