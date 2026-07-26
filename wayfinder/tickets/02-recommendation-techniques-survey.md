---
id: 2
title: Recommendation techniques survey
type: research
status: open
assignee:
blocked-by: []
---

## Question

What recommendation approaches fit Anchor's unusual shape: a per-account taste profile learned from a comparison-derived total ordering (richer than star ratings), which must both (a) rank a few-hundred-film backlog for the ranked tier and (b) score arbitrary never-rated films for the discovery feed?

Survey:

- Content-based methods over TMDB metadata (genres, keywords, credits).
- Embedding-based approaches (film embeddings, taste vectors) and where the embeddings would come from.
- LLM-as-scorer approaches: reading film metadata and judging fit against a taste profile, especially for open-world discovery.
- Hybrids, and how each handles cold start from a seed import.
- Taste-profile representations each approach implies, and how richer multi-criteria comparison signals could be incorporated later.

Recommend candidates with trade-offs for the [Taste profile and scoring design](06-taste-profile-and-scoring.md) ticket.
Produce a markdown summary as a linked asset.
