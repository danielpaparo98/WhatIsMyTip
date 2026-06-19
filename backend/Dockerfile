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
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WORKERS=2 \
    FORWARDED_ALLOW_IPS=10.0.0.0/8

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

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

# Health check to verify the application is running
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Create a startup script that runs migrations then starts the app
RUN echo '#!/bin/sh\n\
set -e\n\
\n\
# Run database migrations\n\
if [ -n "$DATABASE_URL" ]; then\n\
    echo "Running database migrations..."\n\
    alembic upgrade head\n\
    echo "Migrations completed successfully!"\n\
else\n\
    echo "Warning: DATABASE_URL not set, skipping migrations"\n\
fi\n\
\n\
# Start the FastAPI application\n\
echo "Starting FastAPI application..."\n\
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-2} --proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-10.0.0.0/8}\n\
' > /app/start.sh && chmod +x /app/start.sh

# Set the startup script as the entrypoint
CMD ["/app/start.sh"]
