# The stack is Python FastAPI and React TypeScript on PostgreSQL

Anchor is built as a Python backend (FastAPI, SQLAlchemy with Alembic migrations) exposing a JSON API, a React + TypeScript single-page frontend built with Vite and served as static files, and PostgreSQL as the only datastore.
The scorer and feature pipeline use scikit-learn and numpy.
There is no server-side rendering framework: nothing but a landing page needs SEO, and a second server would buy nothing.

Chosen because the recommendation engine is the heart of the product and Python is where it costs least to build well: the weight-vector scorer is a logistic regression scikit-learn provides outright, numpy carries the feature pipeline, and the LLM SDKs are most mature in Python.
The owner is also most fluent in Python and React, so effort goes into the engine rather than the language.
PostgreSQL handles concurrent writes from the web and worker processes, owner-scoped multi-account data, and doubles as the job-queue store (ADR 0009).

## Considered options

- Go backend: rejected; strongest backend-signal value, but a third shallow language for the owner, and every engine feature would cost more code with no product gain.
- Java/Kotlin Spring Boot: rejected; the owner's Java experience is already deep and professionally evidenced, and the ceremony is the heaviest of the options for a solo project.
- TypeScript everywhere (Next.js or Node API): rejected; one language is attractive but backend TypeScript aligns least with the owner's direction, and the engine would lose its native ecosystem.
- SQLite: rejected; fine at this scale for a single process, but concurrent web + worker writers and multi-account scoping make PostgreSQL the boring correct choice.
