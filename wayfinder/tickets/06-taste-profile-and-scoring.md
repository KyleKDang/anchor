---
id: 6
title: Taste profile and scoring design
type: grilling
status: open
assignee:
blocked-by: [2]
---

## Question

The heart of the app.
Decide the taste profile representation and the scoring approach that (a) ranks backlog films for the ranked tier and (b) scores arbitrary never-rated films for the discovery feed.

- Which candidate from the [Recommendation techniques survey](02-recommendation-techniques-survey.md) wins, and why.
- How the comparison-derived ordering (not just band values) feeds the profile.
- Where LLM calls pull real weight vs where classical techniques suffice.
- What "acceptable to some extent" means as an LLM cost guardrail: which features may call LLMs at runtime vs precompute.
- How the profile updates as ratings evolve.
