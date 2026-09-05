# Anchor

A personal movie taste-engine web app: ratings anchored to the films the owner is sure of and ordered by hand on a visible wall, instead of a drifting absolute scale; an automatically managed watchlist; and a recommendation engine that learns each account owner's taste.
The design spec is complete at `docs/design/`; implementation is tracked by the map at issue #21, one ticket per vertical slice.

## Where an issue goes

The map at #21 holds feature slices and nothing else, because it answers one question - how much of the design spec exists - and anything else on it blurs that answer and leaves a map that can never close.

**A feature is a sub-issue of #21.**
That covers the spec's own slices and any feature the spec did not anticipate, which joins the map as a new slice sequenced by GitHub's blocked-by dependencies.

**A bug is a plain repo issue, never a sub-issue of #21.**
A defect in behaviour that already shipped is filed at the top level with the `bug` label, whatever ticket surfaced it and however soon after that ticket merged.
Its body opens with an `## Origin` section naming the ticket it is a bug against ("Bug against #30 (Seed import).") rather than a `## Parent` section, so provenance survives without a parent link.
The [`bug` label](https://github.com/KyleKDang/anchor/issues?q=is%3Aissue+label%3Abug) is how bugs are found; there is no second map.

The dividing question is what the issue changes: a slice of the spec that does not exist yet is a feature, and shipped behaviour that does not match its own ticket is a bug.
Everything reaches `main` the same way regardless, so this is about what the map measures, not about how the work is done.

## Implementing a ticket

`/ship #N` is the whole prompt: it drives one ticket from claim to close, and the ticket carries everything needed, feature or bug.
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
