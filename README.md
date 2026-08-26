# Anchor

A personal movie taste-engine web app: ratings anchored in pairwise comparisons instead of a drifting absolute scale, an automatically managed watchlist, and a recommendation engine that learns each account owner's taste.

The design spec lives in [docs/design/](docs/design/) and the ubiquitous language in [CONTEXT.md](CONTEXT.md).
Implementation is tracked by [issue #21](https://github.com/KyleKDang/anchor/issues/21).

## Layout

- `backend/` - the Python API and the background worker: FastAPI, SQLAlchemy, Alembic, procrastinate on PostgreSQL.
  The web process and the worker run from the same image with different commands.
- `frontend/` - the React + TypeScript single-page app (Vite), served as static files by Caddy.
  `frontend/e2e/` holds the browser smoke suite (Playwright) that runs over the full composed stack.
- `Dockerfile`, `docker-compose.yml`, `Caddyfile` - the composed stack: PostgreSQL, a one-shot migration, the web process, the worker, and Caddy.

## Running the stack

```sh
docker compose up --build --wait
```

Opens the app at <http://localhost> (set `ANCHOR_HTTP_PORT` to move it) and publishes PostgreSQL on port 5433 (`ANCHOR_POSTGRES_PORT`).
`GET /api/health` reports web, database, and worker health; the worker check is a real job round-trip.

## Developing

Backend (needs the compose PostgreSQL running for tests, or `ANCHOR_TEST_ADMIN_DATABASE_URL` pointing at any PostgreSQL that may create databases):

```sh
cd backend
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check && uv run mypy
uv run uvicorn anchor.main:app --reload   # the web process, on :8000
uv run python -m anchor.worker            # the worker process
uv run alembic upgrade head               # migrations
```

Tests speak HTTP to the app over a throwaway real PostgreSQL: each test gets a database cloned from a migrated template, and background jobs run inline in the test (`run_jobs`) or under a real worker (`worker`), per [docs/design/testing.md](docs/design/testing.md).

Frontend:

```sh
cd frontend
npm install
npm run dev          # proxies /api to the backend on :8000
npm run build        # typecheck + production build
npm run smoke        # Playwright, against ANCHOR_BASE_URL (default http://localhost)
```
