# Anchor

A personal movie taste-engine web app: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns each account owner's taste.
The design spec is complete at `docs/design/`; implementation is tracked by the map at issue #21, one ticket per vertical slice.

## Implementing a ticket

`/implement #N` is the whole prompt: the ticket (a sub-issue of the map at #21) carries everything needed.

1. Fetch it (`gh issue view <N>`) and claim it (`gh issue edit <N> --add-assignee @me`) before starting.
2. The ticket is the brief: its Spec citations are required reading, and its Test seam section names where the tests live (the bar is `docs/design/testing.md`).
3. Done means every acceptance criterion verified and the project validation green; then comment on the ticket with how each criterion is met, and close it.

The next ticket is the frontier: open, unassigned, no open blockers.

## Agent skills

### Issue tracker

GitHub Issues on KyleKDang/anchor; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical defaults (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.
