# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────
# whatismytip-api: FastAPI application with automated database migrations.
#
# This single-stage Dockerfile:
#   - Sets up Python 3.12 runtime environment
#   - Installs dependencies using uv for fast, reproducible builds
#   - Runs alembic database migrations on container startup
#   - Starts the FastAPI application with uvicorn
#
# Build:    docker build -t whatismytip-api -f backend/Dockerfile backend/
# Run:      docker run --rm -p 8000:8000 -e DATABASE_URL="..." whatismytip-api
# ──────────────────────────────────────────────────────────────────────

# Use Python 3.12 slim base image
FROM python:3.12-slim

# Set environment variables for Python and the application
# Set to "true" to run alembic migrations on container startup.
# Defaults to "false" — docker-compose runs migrations via the
# init-data container, and CI/CD runs them as a separate step.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WORKERS=2 \
    FORWARDED_ALLOW_IPS=10.0.0.0/8 \
    RUN_MIGRATIONS_ON_START=false

# Explicit ENV declaration for docker inspect visibility
ENV FORWARDED_ALLOW_IPS=10.0.0.0/8

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Trust the DigitalOcean managed-Postgres CA so the backend can verify
# the DB TLS certificate (DB_SSL_VERIFY=true).  This is a PUBLIC CA
# certificate (no private key) shipped in-repo under certs/; adding it
# to the Debian system trust store makes Python's
# ssl.create_default_context() (used by the async engine in
# packages/shared/db.py) trust it automatically.  Must run as root and
# before the USER switch to appuser.
COPY certs/do-managed-postgres-ca.crt /usr/local/share/ca-certificates/do-managed-postgres-ca.crt
RUN update-ca-certificates

# Install uv (Python package installer) for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies using uv
# --frozen ensures exact versions from uv.lock are used
# --no-dev skips development dependencies for smaller production image
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the application code
COPY --chown=appuser:appuser . .

# Install the application itself
RUN uv sync --frozen --no-dev

# Set ownership of application directory
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Add the virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Expose the application port
EXPOSE 8000

# Health check using curl (installed in base image).  curl -f fails
# on HTTP errors (4xx/5xx) which is what we want for liveness checks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Shell-form CMD with conditional migration handling.
# Migrations are gated by RUN_MIGRATIONS_ON_START (default false) to
# prevent race conditions with docker-compose init-data or CI/CD.
CMD ["sh", "-c", "if [ '${RUN_MIGRATIONS_ON_START:-false}' = 'true' ] && [ -n '${DATABASE_URL:-}' ]; then echo 'Running migrations...' && alembic upgrade head || exit 1; fi && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-10.0.0.0/8}"]
