---
id: 1
title: TMDB and Letterboxd data research
type: research
status: open
assignee:
blocked-by: []
---

## Question

What do the TMDB API and the Letterboxd CSV export actually provide, and what constraints do they impose on Anchor's design?

Cover:

- TMDB endpoints for film search, metadata (including plot summaries), images, and catalog-wide discovery/browsing.
- TMDB auth model, rate limits, and terms of use for a multi-account app.
- What taste-relevant data TMDB exposes for the recommender: genres, keywords, credits, similar-films endpoints, and anything embeddings-adjacent.
- The exact fields of a Letterboxd export (ratings CSV, watchlist CSV) and how its rows can be matched to TMDB entries, including failure cases.

Produce a markdown summary as a linked asset.
