---
id: 8
title: Discovery feed design
type: grilling
status: open
assignee:
blocked-by: [6]
---

## Question

Design the discovery feed: recommendations of films the owner has never heard of, as the path for new films to enter the backlog.

- Candidate sourcing from the wider TMDB catalog.
- How the taste profile scores films with zero owner signal, and where LLM judgment fits (the owner favors LLM use here).
- Feed size, refresh cadence, and avoiding repeated rejected suggestions.
- The accept flow (into the backlog) and the reject flow, and how each feeds back into the profile.
- Quarantine: the feed never promotes anything directly into the ranked tier.
