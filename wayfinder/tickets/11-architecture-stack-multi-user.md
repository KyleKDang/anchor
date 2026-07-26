---
id: 11
title: Architecture, stack, and multi-user model
type: grilling
status: open
assignee:
blocked-by: [1, 6]
---

## Question

Decide the architecture, deliberately late now that the recommender's shape is known.

- Hosting: self-hosted vs cloud (left open during charting on purpose).
- Web stack.
- Multi-account model: auth and per-account data isolation, with no interaction between accounts.
- Where the recommendation engine runs: in-process, background jobs, separate service.
- Where LLM calls happen and how costs are bounded per account.
- TMDB metadata caching strategy and rate-limit compliance.
