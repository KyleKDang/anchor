# The demo account

Consolidates wayfinder ticket [Demo account content design (#20)](https://github.com/KyleKDang/anchor/issues/20), as revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)).
The demo account is the shared read-only account the landing page offers, so a visitor (recruiter, friend) can explore a fully lived-in Anchor in under a minute without registering.

## What it is made of

- **A curated fixture, not a snapshot**: a checked-in dataset a build script replays through the real pipelines (import, moves, marks, retrains, verdict precompute); never a copied database.
- **The taste is the developer's real judgments, allowlisted.**
  Default-out: one review pass over the 592-row real export; nothing enters the demo unreviewed.
  Curation rule: **cut for embarrassment, keep for edge** - obscure or embarrassing films go, surprising judgments on recognizable films stay (they are the demo's best material).
  The ordering lands wherever the pass lands, expected in the hundreds.
- **Generic/consensus taste is ruled out**: averaged opinion has no edges, so the prose profile reads like a horoscope and every surface collapses into a popularity list.
  Specificity with visible edges is what makes the surfaces cohere, and coherence between surfaces is the aha.
- **A learn-the-visitor's-taste demo is ruled out**: a 60-second quiz profile sits below the readiness gates' quality floor, and per-visitor profiles mean anonymous-triggered LLM spend plus per-visitor state.
  The "works for you specifically" moment is deliberately placed after signup; the demo's job is to earn it.

## The taste on display

- **Every band carries a pool of recognizable anchors**: real marks where recognizable, a within-band swap to a more famous film where not.
- **A couple of bands are visibly hand-ordered**, so the wall reads as a judgment and not as a sorted export.
- **The prose profile is genuine pipeline output**, never hand-written or text-edited (the product's own rule).
  If it reads flat, fix the fixture - add moves, criteria answers, constraints - and regenerate.
- One or two profile constraints (quality-picker selections) are seeded so the constraints feature is visibly in play.
- The recognizability bias applies where visitors look: ordering extremes, anchors, ranked tier, discovery shelf.
  The middle of the ordering stays as obscure as the real taste is.

## Surfaces

- **Visitors land on the discovery feed** - the hero surface: suggestions with exemplar explanations need zero understanding of Anchor's mechanics to appreciate.
- **No guided tour or welcome overlay**: a demo needing a tour undercuts the self-explanatory claim.
- **Every living state is staged once** so no screen is empty: a few rate-later films, a pin in the up-next zone, a veto, entries on the dismissed list, sync-list rows (films moved across bands since the import), rewatch history on a film page, and a film page with a criteria session's worth of judgment history.

## Build and refresh

- **The fixture authors the outcome; the script replays it.**
  The checked-in fixture states the target - allowlisted films with their bands and within-band order, which films are anchors, what is pinned, vetoed, dismissed, rewatched - and the build imports the ratings, then applies the moves, marks, and actions through the real API and runs the real jobs.
  Staged states are declarative ("this film sits third in its band" becomes the move that puts it there).
  Hand-performed edits rot with schema changes; raw DB rows silently diverge from real engine output.
- **Rebuilt on deploy**; content freshened occasionally by editing the fixture.
  No automated refresh job.
- **All fixture timestamps are relative to build time** ("watched 3 days before build"), so ambient lines never age and the account reads currently lived-in on every rebuild for free.
- **LLM verdicts and the prose profile regenerate only at fixture rebuild** - the only moment the profile can change.
  Build cost lands in the spend ledger under the global/shared scope; runtime LLM cost is exactly zero.

## Read-only enforcement

- **Server-side**: the demo account carries a flag and every write path rejects mutations for it; the UI adapts on top, but the backend is the source of truth.
- **The engine's own maintenance also skips the demo** - restocks (visit-gated, so visitors would otherwise trigger them), rotation, retrains, tier maintenance - so the account stays exactly as built.
- **Write controls are visible but intercepted**: tapping rate, pin, dismiss, and the rest brings up "this is a read-only demo - sign up to build your own" (wording at implementation).
  Edit mode is the one control that is absent rather than intercepted: a wall that invites dragging and then refuses every drop would be a broken toy, not a pitch.
  The verbs are part of the content, and the intercept is where the signup pitch belongs.
  No sandboxed fake writes; the accepted trade-off is that a visitor sees the outcomes of rating (the wall, the judgment history), never the picker itself.
- **One-click enter** from the landing page: a button starts an ordinary session flagged demo.
  The demo account has no credentials and is unreachable through the login form; concurrent visitors are unlimited because nothing writes.
- **Presented neutrally, no owner identity in-product**: "Demo account - a real, lived-in account you can explore."
  No fictional persona, no developer branding; the authorship story lives in the README, portfolio, and interviews.
- The demo account is excluded from every evaluation aggregate ([evaluation.md](evaluation.md)).
