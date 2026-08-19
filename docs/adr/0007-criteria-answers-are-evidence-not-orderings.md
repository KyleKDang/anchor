# Criteria answers are loose evidence, never per-quality rankings

Anchor asks optional criteria questions - "which had the better screenplay?" - about pairs the owner just compared, so per-quality judgments accumulate in the comparison log.
Decided during [Multi-criteria comparison system (#10)](https://github.com/KyleKDang/anchor/issues/10): those answers stay loose evidence about the owner's taste and never build a per-quality ordering, neither shown to the owner nor maintained internally.
The ordering - the overall one - remains the only ranking structure in Anchor.

The reason is evidence volume and structural honesty.
Criteria answers arrive as a trickle: at most one per placement, spread across a dozen-plus qualities, so any single quality gathers dozens of judgments while the library holds hundreds of films.
A ranking built on that is mostly interpolation wearing the costume of a judgment layer, which is exactly what Anchor's overall ordering exists to avoid.
And every additional persisted ordering is a second structure that can drift and contradict, while the drift machinery derives its meaning from tension against one ordering.

What the answers do instead, in two stages: from day one they feed prose-profile evidence lines and the quality picker's inferred suggestions; once a single quality crosses an evidence threshold, a per-quality taste signal activates for discovery reranking and explanations.
They never move the ordering (standing constraint) and never enter the ranked tier's scoring in v1 - overall comparisons already train the overall scorer directly.

Rejected: persisted per-quality orderings (too sparse to trust, a second driftable structure, dilutes the ordering's uniqueness); per-quality weight vectors active from the first answer (noise until evidence accrues, hence the evidence gate); folding per-quality scores into the ranked tier via an aggregation function, the survey's r0 = f(r1, ..., rk) pattern (a second influence channel would blur what moves the queue; revisitable once per-quality evidence is dense).

## Consequences

- The comparison log is the sole store of criteria answers; nothing per-quality is derived into a durable ranking.
- Per-quality taste signals are regenerated artifacts like the rest of the taste profile (ADR 0004): recomputed, never patched, and activation is evidence-gated per quality.
- Explanation and discovery surfaces may say "you consistently favor the better screenplay" but never "your screenplay ranking".
- Revisiting aggregation into the ranked tier score is a fresh decision, not a default future.
