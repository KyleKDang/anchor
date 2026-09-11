# Anchor

A personal movie taste-engine web app: ratings anchored to the films the owner is sure of and ordered by hand on a visible wall, instead of a drifting absolute scale; an automatically managed watchlist; and a recommendation engine that learns each account owner's taste.
The design spec is complete at `docs/design/`, and its initial implementation is done: the map at issue #21 tracked it one ticket per vertical slice, and closed when the last slice shipped.

## Where an issue goes

**Every issue is a standalone top-level issue.**
The map at #21 is closed and takes no new sub-issues; improvements, bug fixes, and features the spec did not anticipate are filed on their own.
Where one ticket has to land before another, say so with GitHub's blocked-by dependencies rather than a parent.

**A bug names what it is a bug against.**
A defect in behaviour that already shipped carries the `bug` label, and its body opens with an `## Origin` section naming the ticket it is a bug against ("Bug against #30 (Seed import).").
Everything else is an `enhancement`: a change to behaviour that ships as its ticket said, or something that does not exist yet.

**Every issue is filed with both labels, a category and a state.**
The category is `bug` or `enhancement`; the state is one of the five in `docs/agents/triage-labels.md`.
An issue written out in full, with its citations and acceptance criteria, is filed `ready-for-agent` in the same breath, because it is already triaged and the label is what `/ship` and the frontier query read.
Leave it `needs-triage` only when it genuinely still needs a decision from the owner.
An issue carrying no state label at all is the failure mode to avoid: it is invisible to every query that looks for work.

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
In `frontend/`: `npm run lint`, `npm run format:check`, `npm run build`.
CI additionally runs the Playwright smoke suite against `docker compose`, and `.github/workflows/ci.yml` is the authority on all of it.

**Code review** means `mattpocock-skills:code-review`, named in full.
The bare `code-review` is Claude Code's built-in, which fans out sub-agents at the session effort level and is not the review this flow asks for.

The next ticket is the frontier: open, `ready-for-agent`, unassigned, no open blockers.

## Agent skills

### Issue tracker

GitHub Issues on KyleKDang/anchor; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical defaults (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.
