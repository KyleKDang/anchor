# One rented box, one codebase, two processes

Anchor runs entirely on a single small rented VPS (Hetzner or DigitalOcean class) under Docker Compose: the web app, a background worker, PostgreSQL, and Caddy as the reverse proxy with automatic HTTPS.
The web app and worker are the same codebase and the same image, started with different commands; the recommendation engine is an imported module called by both, never a separate service.
Background jobs (seed import pipeline, profile regeneration, discovery verdict refresh, TMDB re-sync) run on a Postgres-backed job queue (procrastinate), so enqueuing a job commits in the same transaction as the data change that triggered it.
Deployment is a push to main: GitHub Actions runs tests, builds the image, and deploys to the box.
Nightly pg_dump backups ship off the box to object storage; Sentry's free tier watches both backend and frontend.

Chosen because the app's true shape is one always-on box: the only request-time compute is a logistic-regression scorer that retrains in milliseconds (ADR 0004), everything heavy is batch precompute, and the whole bill stays under roughly $10 a month before LLM spend.
The two-process split keeps a slow batch job from ever making the site laggy, at zero extra operational surface.
The transactional job queue means a crash can never leave data changed but its follow-up job lost.

## Considered options

- Managed platform (Render, Railway, Fly): rejected; least ops, but three billed line items for one box's work, and the owner gains none of the run-your-own-production-box experience.
- Full AWS (ECS + RDS): rejected; strictly more machinery and cost than the app needs, and the owner's resume already carries real AWS work.
- Separate recommendation service: rejected; nothing needs independent scaling, and a second service doubles the ops surface for zero gain.
- Celery + Redis for jobs: rejected; a second datastore to run and back up, and enqueues would not be transactional with the data they follow.
