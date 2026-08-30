# One Dockerfile, two images: `app` (the web API and the worker, same image,
# different commands) and `caddy` (the reverse proxy serving the built frontend).

FROM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The frontend Sentry DSN is baked in at build time; empty (the default) disables reporting.
ARG VITE_SENTRY_DSN
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN
RUN npm run build


FROM python:3.13-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home anchor && chown -R anchor:anchor /app
USER anchor
EXPOSE 8000
CMD ["uvicorn", "anchor.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM caddy:2-alpine AS caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY deploy/Caddyfile /etc/caddy/Caddyfile.prod
COPY --from=frontend-build /frontend/dist /srv
