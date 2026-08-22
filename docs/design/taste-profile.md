# The taste profile and scoring

Consolidates wayfinder tickets [Taste profile and scoring design (#7)](https://github.com/KyleKDang/anchor/issues/7) and [Multi-criteria comparison system (#10)](https://github.com/KyleKDang/anchor/issues/10), adopting the [Recommendation techniques survey (#3)](https://github.com/KyleKDang/anchor/issues/3) recommendation in full.
The architecture is recorded as [ADR 0004](../adr/0004-two-scorer-taste-architecture.md); the survey's full analysis is at [recommendation-techniques.md](../research/recommendation-techniques.md).

## Two scoring jobs, one profile

Anchor has two scoring jobs: rank the backlog into the ranked tier, and judge never-rated films for the discovery feed.
Both are served by one taste profile of three artifacts derived from the ordering, regenerated on change and never incrementally patched:

1. **Weight vector** - the only runtime scorer; ranks the backlog, steers discover slices, prefilters discovery candidates.
2. **Exemplar set** - anchors plus the ordering's extremes; concrete examples for prompts and explanations.
3. **Prose profile** - versioned, LLM-maintained, owner-readable; drives discovery reranking.

## The weight vector

Feature-parameterized Bradley-Terry: logistic regression on TMDB feature differences, trained on pairs sampled from the ordering.
v1 features are symbolic TMDB only - genres, director, top cast, idf-weighted keywords, vote and popularity priors; embeddings remain a later feature-augmentation experiment under the no-training provider rule ([ADR 0003](../adr/0003-tmdb-licensing-posture.md)).
It retrains from scratch on every ordering change, in milliseconds, so it is always current; it scores unseen films by construction and stays inspectable.
LightGBM lambdarank is a named fallback only if the linear scorer measurably plateaus.

### Pair extraction

- All adjacent pairs (they fully capture the order) plus sampled long-range pairs per film (they teach magnitude).
- Explicit comparisons are weighted above implied pairs; provisional placements are down-weighted until graduation; ties train as equality targets.
- No recency decay in v1: the ordering as it stands is the signal, and drift resolution is the one mechanism that owns taste change.
- Exact weights and sample counts are implementation-tunable, validated empirically.

## The exemplar set

Anchors plus the ordering's extremes, recomputed mechanically whenever those change.
It calibrates prompts and grounds every exemplar-based explanation ("Because you loved X and Y").

## The prose profile

The owner-readable description of the owner's taste, LLM-maintained on the mid tier.

- Regenerates on accumulated change (N new placements, an anchor change, a drift-resolution wave, a picker or constraint edit) with a max-staleness backstop; never per comparison.
- Each regeneration bumps the profile version; discovery verdicts are cached keyed by (film, profile version), so the bump is the cache invalidation and the batch-rerank trigger.
- Visible and correctable: the owner can read what Anchor thinks they like and thumb-down claims.
  Corrections persist as profile constraints - structural, never text edits, so regeneration can never clobber them.
  Fully optional.
- Criteria answers and accumulated discovery dismissals feed regeneration as evidence ([ADR 0006](../adr/0006-discovery-dismissals-feed-the-profile.md)); see below and [discovery.md](discovery.md).

## Favored qualities and profile constraints

Favored qualities enter via a quality picker, not owner-authored prose: a skippable multi-select of the account's quality list, pre-checked with suggestions inferred from the owner's judgments (criteria answers make these smarter over time), with free text as an optional escape hatch that adds a custom quality to the list.
Confirm-not-author is the design goal: the owner gets the benefit at near-zero effort.
Selections are stored as profile constraints that every regeneration must respect; constraints with a structural footprint (genre, language) are additionally enforced mechanically in the discovery prefilter.

## The quality system

### The quality list

One canonical list per account behind both the quality picker and criteria questions.
Built-in core: Acting, Screenplay, Direction, Shots, Score, Message (craft); Tension, Pacing, Emotional impact, Ending, Humor, Rewatchability (feel).
Owner-added custom qualities become normal list entries, askable and treated identically.
Craft and feel only: mood-framed qualities ("which would you rewatch *tonight*") are banned; the timeless form (Rewatchability) is the admissible version.
The system never invents list entries.

### Quality tags

Each film gets quality tags - the qualities it is known for - precomputed by LLM on the cheap tier.
Tags are account-independent facts about the film: computed once per film ever, cached, and shared across all accounts, riding under the precompute-only rule and the budget caps.
Tags draw from the built-in vocabulary only; a custom quality is never tagged, so it reaches criteria questions only through the rotation fallback.

### Criteria questions

- **Wording is always a fixed template** ("Which had the better ___?"); the intelligence is in selection, and the LLM never invents qualities or free-form questions.
- **Asked only at the end of a placement** (including re-placements) - never during the comparison loop, drift checks, or anywhere else in v1.
- **Zero or one bonus card per placement**, carrying exactly one question; answering never triggers a second.
- **Non-blocking**: the card sits on the placement-done screen with the two films, Tied, and a small dismiss.
  Tapping an answer, dismissing, or simply doing anything else all cost the same, and ignoring is recorded identically to dismissing.
- **Frequency is adaptive by default** (engagement raises it, non-engagement lowers it), with a manual frequency setting in which adaptive is one option, plus a complete off switch.
- **Pair and quality selection**: reuse a matchup from the just-finished placement; prefer the pair whose films share a quality tag, tie-broken toward the most recent matchup; if no pair overlaps, rotate through the quality list on the last matchup.
- **Every record rides in the append-only comparison log**, unanswered offers included (they drive the adaptive back-off); a contradicting later answer outweighs the earlier one, and nothing is deleted.

### What answers feed

Answers are loose evidence, never per-quality rankings ([ADR 0007](../adr/0007-criteria-answers-are-evidence-not-orderings.md)): no per-quality orderings exist, shown or internal, and the overall ordering stays Anchor's only ranking structure.
Two stages:

1. From day one, answers feed prose-profile evidence lines and the quality picker's pre-checked suggestions.
2. Once a quality crosses an evidence threshold, a per-quality taste signal activates for discovery reranking and explanations.

Answers never move the ordering and never enter the ranked tier's scoring in v1; folding per-quality scores into the tier via an aggregation function is explicitly deferred, revisitable only as a fresh decision.

## Readiness

Three evidence-based states gate the recommendation features; no time component exists.

- **Cold**: too little signal to train anything; no discovery, no ranked tier.
- **Forming**: enough rated films spanning enough bands for a stable weight-vector fit (indicatively ~20 films across 3+ bands; a seed import lands here immediately).
  Discovery lights up.
- **Ready**: enough explicit comparisons that the vector is not dominated by provisional and implied pairs, plus band structure present (indicatively ~50 films and a real explicit-comparison base).
  The ranked tier unlocks.

The gating dimensions (rated-film count, explicit-comparison share, bands spanned) are spec; the numbers are implementation tuning.
[watchlist.md](watchlist.md) consumes *ready* and [discovery.md](discovery.md) consumes *forming* without redefining them.
Readiness is derived from evidence counts, never stored authoritatively.

## LLM guardrails

- **Precompute-only rule**: no interactive screen ever waits on an LLM call.
  All LLM work (discovery reranking, prose regeneration, picker suggestions, quality tags) runs as background batch jobs; the app serves cached results, and explanations are precomputed beside the verdicts they explain.
  Only the worker process imports the LLM module, making the rule a structural property (see [architecture.md](architecture.md)).
  Any future live-call feature is a deliberate written exception.
- **Spend is earned by engagement**: zero spend until an account reaches the *forming* bar (hollow flood accounts cost nothing), refresh cadence driven by activity, never calendar.
- **Two-level caps**: a per-account monthly budget and a global platform-wide monthly cap (values and the spend ledger in [architecture.md](architecture.md)).
  Hitting either degrades gracefully to cached verdicts and classical-scorer ordering - never a broken feed, never a runaway bill.
- **Tiers and provider**: cheap tier (Haiku-class) for listwise reranking and quality tags, mid tier (Sonnet-class) for prose regeneration; v1 targets the Anthropic API behind a provider-agnostic seam, with the swap set constrained by the no-training provider rule (ADR 0003).
