# ──────────────────────────────────────────────────────────────────────
# whatismytip-api: FastAPI application image.
#
# Phase 4 introduces a single long-lived FastAPI process per instance
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
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install uv (pinned for reproducible builds).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# Install dependencies (cached layer — pyproject.toml + uv.lock rarely change).
# --no-install-project keeps the venv lean: we install the project itself
# in the next step so the project's own code is in a separate layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy the rest of the project and install it.
COPY . ./
RUN uv sync --frozen --no-dev


# ---- runtime stage ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

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
# from the nginx reverse proxy; --forwarded-allow-ips=* accepts them
# from any source (lock this down if you put a non-trusted proxy in
# front of the proxy).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
