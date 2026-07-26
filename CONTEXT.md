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
A single pairwise judgment by the owner that one film ranks above another.

**Ordering**:
The total order over all rated films produced by comparisons. The durable, drift-proof layer of the rating system.

**Anchor**:
A film the owner designates as defining the boundary of a band. Fixed reference points: comparisons cannot move them, only the owner can.

**Band**:
A half-star rating bucket (e.g. 3.5). A film's band is determined by which anchors it sits between in the ordering.

**Rating**:
A film's half-star value, derived from its position in the ordering relative to anchors. Never entered directly.
_Avoid_: score (ambiguous with recommender scoring)

**Drift**:
The condition where a film's position in the ordering has become inconsistent with its recorded rating. Detected by the app; resolved by the owner deciding whether the rating or the ordering is stale.

**Seed import**:
The one-time import of an owner's Letterboxd CSV export that bootstraps their ordering.
Anchor has no live Letterboxd connection; this is the only data that crosses over.

**Provisional placement**:
A film's position taken from the seed import rather than derived from comparisons.
Trusted less than comparison-derived positions until refined.

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
