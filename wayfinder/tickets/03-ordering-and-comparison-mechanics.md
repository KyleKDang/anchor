---
id: 3
title: Ordering and comparison mechanics
type: grilling
status: open
assignee:
blocked-by: []
---

## Question

Pin down the precise mechanics of the ordering layer, within the standing constraints (total ordering from pairwise comparisons, owner-controlled anchors, derived ratings, emergent distribution).

- Binary-search insertion details: pivot selection, how many comparisons a placement takes, when the search stops.
- Whether "too close to call" or equal judgments are allowed, and what they do to a total ordering.
- Inconsistency handling: what happens when a new comparison contradicts older ones (cycles), and which judgments win.
- Anchor designation and management: how the owner sets, moves, and retires anchors.
- Band derivation edge cases: films above the top anchor, below the bottom anchor, bands with no anchor yet.
- What comparison history is stored, and whether comparisons ever expire or decay.
