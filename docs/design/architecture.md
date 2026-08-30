# Architecture, stack, and operations

Consolidates wayfinder tickets [Architecture, stack, and multi-user model (#12)](https://github.com/KyleKDang/anchor/issues/12) and [TMDB licensing posture (#15)](https://github.com/KyleKDang/anchor/issues/15).
Decision rationale: [ADR 0008](../adr/0008-python-fastapi-react-postgres-stack.md) (stack), [ADR 0009](../adr/0009-single-box-two-process-architecture.md) (shape), [ADR 0003](../adr/0003-tmdb-licensing-posture.md) (TMDB posture).

## Hosting: one small rented VPS

A single Hetzner/DigitalOcean-class VPS running Docker Compose: the web app, a background worker, PostgreSQL, and Caddy as the reverse proxy with automatic HTTPS.
The app's true shape is one always-on box: the only request-time compute is a logistic-regression scorer that retrains in milliseconds, and everything heavy is batch precompute.
Whole-app cost ceiling: ~$6-8 VPS + ~$1 domain + $0 email + capped LLM spend lands worst-case under $20/month.

- **Deployment is push-to-main**: GitHub Actions runs tests, builds one image, and deploys to the box.
- **Backups**: nightly pg_dump shipped off-box to object storage (Cloudflare R2), driven by a systemd timer on the host rather than an app job so it keeps running even when the app is down; this is the corruption-recovery path ([ADR 0010](../adr/0010-comparison-log-is-evidence-not-event-source.md)).
- **Monitoring**: Sentry free tier on backend and frontend.

## Stack

- **Backend**: Python FastAPI, SQLAlchemy + Alembic migrations, scikit-learn/numpy for the scorer and feature pipeline; JSON API only.
- **Frontend**: React + TypeScript via Vite, served as static files by Caddy; no SSR framework (nothing but a landing page needs SEO).
- **Datastore**: PostgreSQL, the only one.
- **One repository**: `backend/` + `frontend/`.

## One codebase, two processes

The web process and the worker process run from the same image with different commands; the recommendation engine is an imported module called by both, never a separate service.

- Background jobs run on a Postgres-backed queue (procrastinate): transactional enqueue (a data change and its follow-up job commit or fail together), cron-style scheduling, no Redis.
- Jobs: the seed import pipeline, weight-vector retrains, prose regeneration, discovery verdict refresh, TMDB re-sync.

## Accounts and auth

- **Open signup** (the app doubles as a public portfolio piece) with email verification via Resend, plus per-IP rate limits on the signup, login, and verification endpoints.
- **Unverified accounts are fully inert**: no TMDB fetches, no imports, no rows beyond the account record.
  No CAPTCHA in v1: with the verification gate plus engagement-earned LLM spend, mass signup buys an attacker nothing but inert rows.
- **Auth is hand-built**: argon2 password hashing, server-side sessions in Postgres, httpOnly cookies.
  Managed auth and JWTs are rejected in the ticket: no product gain at this scale, and server-side sessions revoke instantly.
- **Isolation**: a single database; every user-owned table is scoped by `account_id` and every query filtered by the logged-in account.
  Shared catalog tables (film metadata, per-film quality tags) are deliberately unscoped.
- **Demo account**: the landing page offers a shared read-only demo account (flagged in the account record, no credentials, unreachable through the login form).
  Its content and enforcement are specified in [demo-account.md](demo-account.md).

## LLM plumbing

- **The seam is operations-shaped**: one internal module exposing Anchor's actual jobs - `rerank_candidates`, `regenerate_prose_profile`, `tag_film_qualities`, `suggest_qualities` - each schema-validated; not a generic prompt wrapper.
  One adapter per provider behind it; v1 ships Anthropic only (cheap tier for reranking and tags, mid tier for prose, Message Batches for refreshes).
- **The no-training provider allowlist (ADR 0003) is enforced in code at this seam.**
- **Only the worker imports the module**, making the precompute-only rule ([taste-profile.md](taste-profile.md)) a structural property, not a convention.
- **Spend ledger**: every LLM call writes a row (account or shared scope, operation, model, tokens, computed cost) to Postgres.
  The seam checks month-to-date totals before dispatch: per-account cap **$2/month**, global cap **$10/month**, both config values.
  Hitting either skips regeneration and serves cached results - the invisible degradation already designed into discovery.
  Dormant accounts cost ~nothing structurally, since every refresh is activity-driven.

## TMDB integration and compliance

- **Shared local film store** keyed by TMDB id, filled by one bundled `append_to_response` call per film, each row stamped with a fetch timestamp.
- **Rolling re-sync**: a job refreshes still-referenced films older than ~5 months (inside the 6-month terms ceiling); unreferenced rows go stale and re-fetch on next use.
- **One shared TMDB client**, self-throttled to a few requests per second (far under the ~40/s soft limit) with automatic 429 backoff-and-retry.
- **Images are hotlinked** from TMDB's CDN; only paths are stored, never image bytes.
- **Attribution stays on**: the TMDB logo and the "not endorsed, certified, or otherwise approved by TMDB" notice appear in the app (Profile screen) and wherever the portfolio shows it.

### Licensing posture (ADR 0003)

Anchor holds a good-faith non-commercial posture, recorded honestly as a risk-accepted interpretation: TMDB metadata is used fully - as scorer features and LLM prompt context - in personal, unmarketed, non-revenue software, and the realistic worst case (a revoked API credential) is accepted.

- **The no-training provider rule**: TMDB content and the owner's taste profile are sent only to AI providers whose terms bar training on customer API inputs by default.
  Verified 2026-08-02 ([llm-provider-data-use.md](../research/llm-provider-data-use.md)): Anthropic API, OpenAI API, and Gemini paid tier qualify; Gemini free tier and Voyage AI at defaults do not.
  Every future provider gets the same check, re-verified at integration time.
- **Four bright lines**: no training runs with TMDB content as corpus; no public redistribution of TMDB-derived data; attribution on; the 6-month cache ceiling honored as the rolling refresh above.
- **Named fallback, not pre-built**: if the strict reading ever wins, recommender features refill from the MovieLens tag genome (links.csv join) plus Wikidata; display metadata from Wikidata/OMDb; posters partially from Wikimedia.
  No abstraction layers are built now: imported TMDB metadata lives in Anchor's own tables, so revocation stops refreshes without bricking the catalog.

## Implementation prerequisite

Register a domain for the app - needed for HTTPS and for Resend's sender verification.
It gates no design decision; it is simply the first errand of implementation.
