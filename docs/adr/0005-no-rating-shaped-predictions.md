# No rating-shaped predictions on unwatched films

**Framing amended by [ADR 0013](0013-the-ordering-is-edited-by-hand.md) on 2026-09-05**: ratings are now placed on a band picker and corrected by moves on the wall rather than emerging from comparisons, and the decision stands unchanged, because a prediction seen before watching would steer the pick and the move exactly as it would have steered a comparison.

The taste profile's weight vector scores any film instantly, and anchors calibrate those scores into half-star bands, so Anchor can always compute "you would probably rate this ~4.0" for an unwatched film.
Decided during [Ranked tier maintenance policy (#8)](https://github.com/KyleKDang/anchor/issues/8): that prediction is never shown anywhere, in any rating-shaped form - no predicted band, no predicted stars, not even behind an on-demand reveal.

The reason is judgment purity.
Ratings in Anchor are never entered directly; they emerge from pairwise comparisons, and the ordering those comparisons build is the product's one honest, drift-proof layer.
A prediction seen before watching plants an expectation that can tilt the comparison answers themselves, and a bias baked in at judgment time is invisible to drift detection and can never be washed out of the ordering afterward.
The ballpark-guess safeguards do not cover this: they keep a guess from setting the rating, but they cannot keep an expectation from steering the judgments.
An on-demand reveal fails the same way, because the owner about to watch a film is exactly the owner who will tap it.

A film's position on the ranked tier is the entire public statement about it.
Where a "why is this here" surface is wanted, it must speak in non-rating vocabulary - position, and exemplar-based explanations like "because you loved these" - never in stars or bands.

Rejected: a predicted-band annotation on tier films (free explanatory value and it exercises the anchor calibration, but the calibration payoff is not worth contaminated future judgments), and grouping the tier by predicted band (same problem, and it hides the order, which is the tier's job).

## Consequences

- Binds every surface that shows unwatched films: the ranked tier, the backlog, the discovery feed (#9), and search results.
- Rating-shaped output stays internal to the scorer and the recommendation pipeline, where it remains fully available.
- Explanation surfaces (owned by Core flows in prose, #11) design within non-rating vocabulary from the start.
