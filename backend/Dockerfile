# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────
# whatismytip-api: FastAPI application image.
#
# Phase 4+ introduces a single long-lived FastAPI process per instance
# (replacing the 8 DigitalOcean Functions from the FaaS architecture).
# The image is multi-stage:
#
#   * builder  — installs the project + its dependencies into a uv-managed
#                venv at /app/.venv (cached layer)
#   * runtime  — slim Python image, copies the prebuilt venv, runs as a
#                non-root user, exposes port 8000, has a /health probe.
#
# Build:    docker build -t whatismytip-api -f backend/Dockerfile backend/
# Run:      docker run --rm -p 8000:8000 whatismytip-api
# ──────────────────────────────────────────────────────────────────────

# ---- builder stage ----
# TODO: pin to actual digest (`docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim`)
ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install uv (pinned for reproducible builds).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# Image self-identification (visible in `docker inspect` and App
# Platform's UI).  Override at build time:
#   --build-arg IMAGE_TAG=$(git rev-parse HEAD)
ARG IMAGE_TAG=dev
LABEL org.opencontainers.image.source="https://github.com/danielpaparo98/WhatIsMyTip" \
      org.opencontainers.image.revision="${IMAGE_TAG}" \
      org.opencontainers.image.title="whatismytip-api" \
      org.opencontainers.image.description="FastAPI backend for WhatIsMyTip"

# Install dependencies (cached layer — pyproject.toml + uv.lock rarely
# change).  BuildKit cache mount keeps the uv wheel cache across
# rebuilds so we don't re-download wheels for every code change.
# --no-install-project keeps the venv lean: we install the project
# itself in the next step so the project's own code is in a separate
# layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the project and install it.
COPY . ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# ---- runtime stage ----
FROM ${PYTHON_BASE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

# uvicorn worker count.  Override at runtime:
#   -e WORKERS=4  (App Platform spec)  or
#   -e WORKERS=2  (local docker compose)
# 2 is the default for basic-xxs; bump to 4 on apps-s-1vcpu-1gb.
ENV WORKERS=2

# Trusted upstream proxies for X-Forwarded-* headers.  Locked to the
# App Platform private network (10.0.0.0/8) by default; override via
# -e FORWARDED_ALLOW_IPS="..." in environments where the proxy lives
# elsewhere.  Using '*' (the upstream uvicorn default) is a security
# risk: it lets any client forge the X-Forwarded-For header.
ENV FORWARDED_ALLOW_IPS=10.0.0.0/8

WORKDIR /app

# Create a non-root user to run the app.
RUN groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy the prebuilt venv + project from the builder stage.
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000

# Container-level liveness probe — hits the FastAPI /health endpoint.
# The app returns 200 in all cases (degraded/healthy) so this is
# always green while the process is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Run uvicorn.  --proxy-headers makes it trust X-Forwarded-* headers
# from the nginx reverse proxy; --forwarded-allow-ips restricts which
# upstream IPs are allowed to set them.  Default is the App Platform
# private network (10.0.0.0/8); override with -e FORWARDED_ALLOW_IPS
# in environments where the proxy lives in a different subnet.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-10.0.0.0/8}"]
