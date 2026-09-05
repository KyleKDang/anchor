# Testing decisions

Fixes the test seams and the test-quality bar for implementation.
Decided at implementation ticket-slicing (2026-08-22), after the design map closed, and revised on 2026-09-05 by the direct-ordering redesign ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)); every implementation ticket names the seam its tests run at.
Vocabulary follows [CONTEXT.md](../../CONTEXT.md).

## The seams

Three fakes, one assertion surface; behavior is asserted at the highest seam that stays deterministic.

### The JSON API over a real PostgreSQL (primary)

Every behavior test speaks HTTP to the FastAPI app backed by a throwaway real PostgreSQL, and asserts on API responses and database state.
Background jobs run inline inside the test, so a flow that spans the web and worker processes is still one test.
This is the exact surface the frontend consumes, so tests survive UI redesigns; the spec deliberately defers visual design to implementation-time prototyping.
No mocking exists inside the engine: the recommendation engine is an imported module and is exercised only through the API and jobs.

One exception rides below this seam: the weight-vector trainer gets direct module tests for held-out accuracy and feature-pipeline correctness, because scorer quality is not meaningfully an API behavior.

### The LLM operations seam

The operations-shaped module from [architecture.md](architecture.md) (`rerank_candidates`, `regenerate_prose_profile`, `tag_film_qualities`, `suggest_qualities`) is the fake boundary.
Tests script a fake adapter per test with canned verdicts, tags, and prose, so discovery and prose-profile behavior is deterministic and free.
No automated test ever calls a real provider; the real Anthropic adapter gets at most a tiny manual smoke check.

### TMDB and Resend at the HTTP edge

TMDB is faked with canned JSON responses at the shared client's HTTP boundary: the bundled per-film call and the discover, similar, and recommendations endpoints.
Import-matcher tests use fixtures derived from the real 592-row export plus the synthetic edge rows [onboarding-and-import.md](onboarding-and-import.md) names: NBSP, en-dash, and middle-dot titles, commas in titles, accents, missing years, TV-side rows, deleted films, duplicate title+year.
Resend is faked the same way; dev and test environments never send mail.

### The browser smoke suite

A thin Playwright suite over the full running stack covers wiring, not behavior: a handful of journeys (sign up, import, rate a film, move a film on the wall, see the watchlist; the demo account rejects writes).
It stays capped at a handful of journeys; all behavior coverage lives at the API seam.

## What makes a good test here

- Assert owner-visible outcomes and spec invariants, never the advisory math's internals.
  What is left of the advisory math - the picker's choice of opponent, pair sampling - is advisory-only ([ADR 0001](../adr/0001-explicit-ordering-not-model-derived.md)), so tests pin what it must not do - nothing on the wall moves except through the owner's picks, moves, and re-rates - and never which opponent it happened to pick.
  Consequence for the code: anything sampled (opponent selection, pair extraction) accepts a seed, so a scripted answer sequence lands deterministically.
- No calendar time to fake: every cooldown and staleness measure is denominated in the watch clock or the refresh counter, so tests advance state by logging watches and refreshes, never by freezing a clock.
- The cross-cutting invariants of [data-model.md](data-model.md) are shared assertion helpers run after mutating flows: ranks dense within every band and every band one of the ten values, an anchor always in the band it was marked in, the comparison log append-only and status-free, nothing rating-shaped in any API response for an unwatched film, every account-realm row owner-scoped.
- Tests read as flows in CONTEXT.md vocabulary (rate, narrow a range, move, re-rate, mark an anchor), not as per-endpoint unit tests.
