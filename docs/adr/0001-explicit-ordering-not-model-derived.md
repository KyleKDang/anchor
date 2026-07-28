# The ordering is an explicit stored sequence, not a model-derived ranking

The ordering could be derived from a probabilistic strength model (TrueSkill lineage, per-item Bradley-Terry as in choix): self-correcting, but every comparison nudges estimates, so films can swap positions implicitly and anchors would need special pinning.
We instead persist the ordering as an explicit sequence of tie-group slots, and demote the probabilistic machinery to advisory-only: it selects informative comparison opponents, judges placement confidence, and raises drift flags, but can never reorder the sequence.
Chosen because a film's position and rating must never change behind the owner's back; wrong placements are corrected through owner-resolved drift flags, not silent model updates.

## Considered options

- Model-derived ordering (sort by estimated strength): rejected; implicit reordering violates the drift-proof guarantee.
- Explicit sequence with no probabilistic layer at all: rejected; uncertainty tracking is too useful for opponent selection, stopping, and drift detection to discard.
