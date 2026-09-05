# The ordering is edited by hand: band rows, anchor pools, and coarse within-band order

Supersedes [ADR 0002](0002-anchors-are-centroids-with-derived-dividers.md).
Amends the pair-extraction rule of [ADR 0004](0004-two-scorer-taste-architecture.md), the framing of [ADR 0005](0005-no-rating-shaped-predictions.md) and [ADR 0007](0007-criteria-answers-are-evidence-not-orderings.md), and the list of primary state in [ADR 0010](0010-comparison-log-is-evidence-not-event-source.md).
Decided 2026-09-05, after real use of the shipped placement and settling flows on an imported library.

The original design found every film's position through pairwise comparisons: a placement was a bisection of the ordering, an import seeded provisional tie-groups that had to be settled one comparison at a time, and a rating was derived from where a film's position fell against dividers pinned by band judgments.
In use, settling an imported library of a few hundred films needed thousands of answers before the recommendation features unlocked, and the answers themselves were often forced: between two films of roughly the same standing the owner has no honest preference, and every such answer was noise entering the one layer that must stay honest.
The ordering was also invisible while it was being built, so the owner could not see what their answers were doing.

We decided the ordering is edited directly.

- **The ordering is ten band rows, each a strict order.**
  A film's band is its rating, chosen by the owner and stored; its rank inside the band is where the owner put it, or the default order (TMDB average, shrunk where votes are few) until they do.
  There are no ties and no dividers: the row is the band, and its ends are the boundaries.
- **The owner rates on a band picker and corrects on a wall.**
  The picker shows each band's anchors, so a pick is made against the owner's own references; an owner unsure between adjacent bands selects the range and a few comparisons against those bands' anchors narrow it.
  Corrections are drags in the wall's edit mode, saved on every drop.
  Nothing else writes the ordering; the engine never suggests, queues, or performs a move.
- **Anchors are pools of certain films, not centroids.**
  Any number per band; an anchor bounds a comparison and never acts as a floor or ceiling, because the pool of a top band is naturally its best films and the pool of a middle band its typical members.
  The seam between two bands is settled by the boundary films, through the boundary question.
  An anchor moved to another band is retired, since a reference that moved is no longer certain.
- **Within-band order is a range to the engine.**
  Training pairs across bands carry full weight; pairs within a band are weighted by the distance between the films as a fraction of the band, so neighbours train as near-equals and only real separation counts.
  This is what lets a strict order stand in for the judgments the owner cannot actually make between neighbours.
- **Comparisons survive as criteria questions**: a run after a placement and an open-ended session from a film's page, feeding only the taste profile.
  They are the optional way to teach Anchor about a film, never the price of a rating.

Chosen because the product's thesis is that ratings should be anchored to reference films rather than a drifting absolute scale, and the pools plus the visible wall deliver that at a fraction of the cost: the owner picks while looking at their own definitive films and corrects while looking at the whole ordering, which is a better guard against drift than blind comparisons whose effect they could not see.
The cost is that a single-band pick is an absolute judgment again; the pools on the picker, the range for real uncertainty, and the wall's edit mode are the mitigations, and the whole settling, drift, and divider apparatus is the price no longer paid.

## Consequences

- ADR 0001 stands and is strengthened: the ordering is explicit persisted state, and the owner's acts - picks, moves, re-rates, marks - are the only writers.
- ADR 0002 is superseded: ratings are stored on the placement as the owner's chosen band, and no divider exists.
- ADR 0004's pair extraction changes as above; the provisional discount and tie targets are gone, and explicit band comparisons that contradict the ordering as it stands are dropped rather than marked.
- ADR 0005 stands: a predicted band would steer a pick and a move exactly as it would have steered a comparison.
- ADR 0007 stands, with criteria questions now asked in runs and sessions rather than once per placement; they still never build a per-quality ranking.
- ADR 0010 stands: the placements and anchor marks are primary state, the log is evidence, and the log's status column is gone, since a judgment the ordering moved past is simply read against the ordering as it stands.
- Removed outright: tie-groups, dividers, sliver questions, provisional placements, settling, keep-comparing, drift flags, drift checks, and the position-only state.
- A seed import unlocks discovery and the ranked tier the moment it holds enough films, since readiness now measures films and bands only.

## Considered options

- Keep settling with a lighter ladder (fewer questions per film, better opponent choice): rejected; the cost is in the film count, not the per-film cost, and no ladder makes thousands of answers a reasonable price for a feature.
- Model-derived within-band order (sort each band by the advisory math): rejected by ADR 0001; a film's position must never change behind the owner's back.
- A mandatory plus-or-minus-half-star check after every pick: rejected; with the pools on screen the check has already happened, and a question after every rating is the chore returning.
- Keep ties as an explicit answer: rejected; tie-groups swallowed whole imports, and most pairs the owner could not separate were not equal but unranked, which the coarse engine reading expresses better.
- Keep drift detection over the direct ordering: rejected; drift existed because the ordering was invisible and only comparisons could move it, and a visible wall the owner edits by hand has no need for a second correction channel.
