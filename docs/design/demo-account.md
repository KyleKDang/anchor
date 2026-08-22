# The demo account

Consolidates wayfinder ticket [Demo account content design (#20)](https://github.com/KyleKDang/anchor/issues/20).
The demo account is the shared read-only account the landing page offers, so a visitor (recruiter, friend) can explore a fully lived-in Anchor in under a minute without registering.

## What it is made of

- **A curated fixture, not a snapshot**: a checked-in dataset a build script replays through the real pipelines (import, placements, retrains, verdict precompute); never a copied database.
- **The taste is the developer's real judgments, allowlisted.**
  Default-out: one review pass over the 592-row real export; nothing enters the demo unreviewed.
  Curation rule: **cut for embarrassment, keep for edge** - obscure or embarrassing films go, surprising judgments on recognizable films stay (they are the demo's best material).
  The ordering lands wherever the pass lands, expected in the hundreds.
- **Generic/consensus taste is ruled out**: averaged opinion has no edges, so the prose profile reads like a horoscope and every surface collapses into a popularity list.
  Specificity with visible edges is what makes the surfaces cohere, and coherence between surfaces is the aha.
- **A learn-the-visitor's-taste demo is ruled out**: a 60-second quiz profile sits below the readiness gates' quality floor, and per-visitor profiles mean anonymous-triggered LLM spend plus per-visitor state.
  The "works for you specifically" moment is deliberately placed after signup; the demo's job is to earn it.

## The taste on display

- **All ten bands anchored** with recognizable films: real designations where recognizable, a within-band swap to a more famous film where not.
- **The prose profile is genuine pipeline output**, never hand-written or text-edited (the product's own rule).
  If it reads flat, fix the fixture - add comparisons, criteria answers, constraints - and regenerate.
- One or two profile constraints (quality-picker selections) are seeded so the constraints feature is visibly in play.
- The recognizability bias applies where visitors look: ordering extremes, anchors, ranked tier, discovery shelf.
  The middle of the ordering stays as obscure as the real taste is.

## Surfaces

- **Visitors land on the discovery feed** - the hero surface: suggestions with exemplar explanations need zero understanding of Anchor's mechanics to appreciate.
- **No guided tour or welcome overlay**: a demo needing a tour undercuts the self-explanatory claim.
- **Every living state is staged once** so no screen is empty: exactly one open drift flag (a recognizable film, the contradiction graspable at a glance - a wall of flags reads as "the system is confused"), a few rate-later films, some provisional placements still settling, a pin in the up-next zone, a veto, entries on the dismissed list, sync-list rows, and rewatch history on a film page.

## Build and refresh

- **The fixture authors the outcome; the script derives the inputs.**
  The checked-in fixture states the target - allowlisted films with ratings, ordering adjustments, which film drifts, what is pinned - and the build derives a consistent placement-answer sequence and replays it through the real engine.
  Staged states are declarative ("this film has an open drift flag" becomes fabricated contradicting answers that honestly produce it).
  Hand-performed placements rot with schema changes; raw DB rows silently diverge from real engine output.
- **Rebuilt on deploy**; content freshened occasionally by editing the fixture.
  No automated refresh job.
- **All fixture timestamps are relative to build time** ("watched 3 days before build"), so ambient lines never age and the account reads currently lived-in on every rebuild for free.
- **LLM verdicts and the prose profile regenerate only at fixture rebuild** - the only moment the profile can change.
  Build cost lands in the spend ledger under the global/shared scope; runtime LLM cost is exactly zero.

## Read-only enforcement

- **Server-side**: the demo account carries a flag and every write path rejects mutations for it; the UI adapts on top, but the backend is the source of truth.
- **The engine's own maintenance also skips the demo** - restocks (visit-gated, so visitors would otherwise trigger them), rotation, retrains, tier maintenance - so the account stays exactly as built.
- **Write controls are visible but intercepted**: tapping rate, pin, dismiss, and the rest brings up "this is a read-only demo - sign up to build your own" (wording at implementation).
  The verbs are part of the content, and the intercept is where the signup pitch belongs.
  No sandboxed fake writes; the accepted trade-off is that a visitor sees placement's outcomes (ordering, comparison log), never the flow itself.
- **One-click enter** from the landing page: a button starts an ordinary session flagged demo.
  The demo account has no credentials and is unreachable through the login form; concurrent visitors are unlimited because nothing writes.
- **Presented neutrally, no owner identity in-product**: "Demo account - a real, lived-in account you can explore."
  No fictional persona, no developer branding; the authorship story lives in the README, portfolio, and interviews.
- The demo account is excluded from every evaluation aggregate ([evaluation.md](evaluation.md)).
