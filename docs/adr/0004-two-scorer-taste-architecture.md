# The taste engine is two scorers over a three-artifact profile, with LLMs precompute-only

**Amended by [ADR 0013](0013-the-ordering-is-edited-by-hand.md) on 2026-09-05**: the pair-extraction rule below now reads the ordering as band rows, with within-band pairs weighted by distance and no provisional discount or tie targets; everything else stands.

Anchor has two scoring jobs: rank the backlog into the ranked tier, and judge never-rated films for the discovery feed.
We decided both are served by one taste profile made of three artifacts derived from the ordering: a feature-weight vector (logistic regression on TMDB feature differences, the feature-parameterized Bradley-Terry form, trained on pairs sampled from the ordering), an exemplar set (anchors plus ordering extremes), and a versioned owner-readable prose profile maintained by an LLM.
The weight vector is the only runtime scorer; all LLM work (discovery listwise reranking, prose regeneration, picker suggestions) is precompute-only, batch-scheduled, and never sits in an interactive request path.
Chosen because the linear scorer consumes the ordering natively, scores unseen films by construction, retrains from scratch in milliseconds, and stays inspectable, while the LLM cascade covers exactly the open-world judgment a symbolic scorer cannot, at a bounded and predictable cost.

Cost is guarded structurally rather than by trust: LLM spend is earned by engagement (no spend until an account's taste profile reaches the discovery readiness bar, refresh cadence driven by activity, so hollow accounts cost nothing), capped per account monthly and platform-wide monthly, and both caps degrade to cached verdicts plus classical-scorer ordering rather than breaking the feed.
Training pairs come from the ordering as all adjacent pairs plus sampled long-range pairs, explicit comparisons weighted above implied pairs, provisional placements down-weighted, ties as equality targets; no recency decay (drift resolution is the one mechanism that corrects for taste change).
The profile regenerates rather than patches: weight vector and exemplar set on every relevant change, prose profile on accumulated change with a staleness backstop, each prose regeneration bumping the profile version that keys cached discovery verdicts.

## Considered options

- LightGBM lambdarank for backlog ranking: rejected first-choice; overfit-prone on a single few-hundred-item query and opaque; named fallback if the linear scorer measurably plateaus.
- Embedding features in v1: rejected; symbolic TMDB features only, embeddings remain a later feature-augmentation experiment under the no-training provider rule (ADR 0003).
- Prose-only or vector-only profile: rejected; each artifact serves a job the others cannot (instant scoring, calibration and explanation, open-world judgment).
- Interactive LLM calls (live explanations, on-demand scoring): rejected for v1; every explanation is precomputed beside the verdict it explains, so cost stays a function of refresh cadence, not usage spikes.
- Recency-weighted training pairs: rejected for v1; a second silent correction channel would blur which mechanism owns taste change.
