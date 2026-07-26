---
label: wayfinder:map
title: Anchor design map
---

# Anchor design map

## Destination

A complete design spec for Anchor: a set of markdown docs in this repo covering the domain model, rating and ordering mechanics, watchlist lifecycle, recommendation engine, discovery feed, data model, architecture and stack, and every required flow described in prose.
Complete enough that implementation planning can start without reopening design questions.
Visual/UI design is explicitly excluded; implementation sessions will prototype multiple UI directions instead of pre-picking one.

## Notes

- Domain: personal movie taste engine.
  Complements Letterboxd but is not connected to it; the one-time seed import is the only crossover.
- Ubiquitous language lives in [CONTEXT.md](../CONTEXT.md).
  Keep it current via the domain-modeling skill as tickets resolve.
- Skills to consult per session: grilling and domain-modeling for HITL tickets, research for AFK research tickets, prototype when a ticket needs a concrete artifact to react to.
- The destination is itself a document set, so the final [Spec assembly](tickets/13-spec-assembly.md) ticket executes (writes the spec) rather than decides.
  That override of plan-don't-do is intentional.

### Standing constraints (pre-decided, do not relitigate)

- Pairwise comparisons produce a total ordering of everything rated.
  The ordering is the durable, drift-proof layer.
- Half-star band boundaries come from owner-designated anchor films.
  Comparisons cannot move anchors; only the owner can.
- A new film's rating is derived, never asked for directly: the comparison flow binary-searches it into the ordering and the anchors it lands between determine its band.
- The rating distribution is emergent.
  Never force or normalize it to a curve.
- Drift detection: when a film's position becomes inconsistent with its recorded rating, flag it and ask the owner whether the rating or the ordering is stale.

### Settled during charting (2026-07-26)

- Cold start: one-time Letterboxd CSV seed import producing provisional placements, refined by comparisons afterward.
- The ranked tier draws only from the backlog, but a separate discovery feed recommending never-backlogged films is a first-class feature, not an extra.
- Multi-account platform: separate data per account, no interaction between accounts.
- Paid LLM API calls are acceptable to some extent; favored especially for discovery over films the owner has never rated.
- Hosting is an open architecture decision, deliberately resolved late once the recommender's shape is clear.
- TMDB is the metadata source; plot summaries are hidden behind a spoiler toggle.

### Tracker conventions (local-markdown fallback)

- This map: `wayfinder/map.md`.
  Tickets are its children: `wayfinder/tickets/NN-slug.md`.
- Ticket frontmatter: `id`, `title`, `type` (`research` | `prototype` | `grilling` | `task`), `status` (`open` | `closed`), `assignee` (empty = unclaimed; claim by setting it before any work), `blocked-by` (list of ticket ids).
- A ticket is unblocked when every id in `blocked-by` is closed.
  The frontier = open, unblocked, unassigned tickets.
- To resolve: append a `## Resolution` section to the ticket, set `status: closed`, and add a one-line entry to Decisions so far below.
- Never resolve more than one ticket per session.

## Decisions so far

<!-- one line per closed ticket: [title](tickets/NN-slug.md) - gist of the answer -->

(none yet)

## Not yet specified

- LLM usage guardrails: which features may call LLMs at runtime vs precompute, and what "acceptable to some extent" means as a cost ceiling.
  Sharpens inside the taste profile and multi-criteria tickets.
- Recommender quality: how to judge whether the ranked tier and discovery feed are actually good, and what feedback signals beyond ratings (skips, "not tonight" behavior, vetoes) feed back into the profile.
  Sharpens after taste profile design.
- Surfacing cadence: how drift flags, re-rank events, and promotions are presented without nagging.
  Sharpens after the ranked tier and drift tickets.
- Onboarding for a fresh account with no seed import: first-run experience and anchor selection guidance.
  Sharpens after seed import and ordering mechanics resolve.
- Spec document structure: how the final doc set is organized.
  Sharpens near Spec assembly.

## Out of scope

- Social interaction between accounts (accounts exist; interaction does not).
- Live Letterboxd integration or write-back; only the one-time seed import crosses over.
- Streaming availability info (a plain search answers it).
- Per-film notes or reviews (Letterboxd owns that habit).
- Watch-date diary UI (Anchor timestamps watches internally, but no diary feature).
- TV shows.
- Visual/UI design and styling: deferred to implementation sessions, which should prototype multiple UI directions rather than pre-picking one.
