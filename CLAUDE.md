# Anchor

A personal movie taste-engine web app: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns each account owner's taste.
The design spec is complete at `docs/design/`; implementation is tracked by the map at issue #21, one ticket per vertical slice.

## Implementing a ticket

`/ship #N` is the whole prompt: it drives one ticket from claim to close, and the ticket (a sub-issue of the map at #21) carries everything needed.
`/ship` owns the sequence; this file owns what is specific to Anchor.

**The brief.**
The ticket's Spec citations are required reading, and its Test seam section names where the tests live.
The bar is `docs/design/testing.md`.

**Branching and merging.**
`main` is protected and every push to it deploys, so code reaches it only through a rebase-merge of a PR that links the ticket (`Closes #N`); the branch then auto-deletes.
Doc and config one-liners may go straight to `main`.

**Validation green.**
In `backend/`: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`.
In `frontend/`: `npm run build`.
CI additionally runs the Playwright smoke suite against `docker compose`, and `.github/workflows/ci.yml` is the authority on all of it.

**Code review** means `mattpocock-skills:code-review`, named in full.
The bare `code-review` is Claude Code's built-in, which fans out sub-agents at the session effort level and is not the review this flow asks for.

The next ticket is the frontier: open, unassigned, no open blockers.

## Agent skills

### Issue tracker

GitHub Issues on KyleKDang/anchor; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical defaults (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.
