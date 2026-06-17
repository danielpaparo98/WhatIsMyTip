#!/bin/bash
set -euo pipefail

# Colors for output (printed via printf for portability — LO-005).
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

printf '%s=== WhatIsMyTip Database Setup ===%s\n' "$GREEN" "$NC"

# Change to project root
cd "$(dirname "$0")/.."

# Load environment variables
if [ -f .env ]; then
    printf '%sLoading .env...%s\n' "$YELLOW" "$NC"
    set -a
    source <(grep -v '^#' .env | grep -v '^$')
    set +a
else
    printf '%sError: .env file not found%s\n' "$RED" "$NC" >&2
    printf 'Copy .env.example to .env and configure your settings.\n' >&2
    exit 1
fi

# Check for DATABASE_URL (LO-014: fail fast with a clear message
# rather than letting alembic fail with an opaque error).
: "${DATABASE_URL:?DATABASE_URL must be set in .env}"

# Use psql via PGPASSWORD / PGHOST / PGPORT / PGUSER / PGDATABASE
# env vars (set by psql itself from the connection URI) so the
# password never appears in argv (visible in `ps` / `/proc`) and
# special characters in the password are handled correctly.  (ME-002)
extract_pg_env() {
    # Python is always available in the dev venv; use it to parse
    # the URI cleanly.
    python - <<PY
import os, urllib.parse
u = urllib.parse.urlparse(os.environ["DATABASE_URL"])
os.environ["PGHOST"]     = u.hostname or "localhost"
os.environ["PGPORT"]     = str(u.port or 5432)
os.environ["PGUSER"]     = u.username or ""
os.environ["PGPASSWORD"] = u.password or ""
os.environ["PGDATABASE"] = (u.path or "/").lstrip("/")
for k in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
    print(f"export {k}={os.environ[k]!r}")
PY
}

# Print a redacted summary only (don't echo the password — ME-002).
DB_NAME=$(python -c "import os, urllib.parse; print((urllib.parse.urlparse(os.environ['DATABASE_URL']).path or '/').lstrip('/'))")
DB_HOST=$(python -c "import os, urllib.parse; print(urllib.parse.urlparse(os.environ['DATABASE_URL']).hostname or 'localhost')")
DB_PORT=$(python -c "import os, urllib.parse; print(urllib.parse.urlparse(os.environ['DATABASE_URL']).port or 5432)")
printf '%sDatabase: %s on %s:%s%s\n' "$YELLOW" "$DB_NAME" "$DB_HOST" "$DB_PORT" "$NC"

# Check if psql is available
if command -v psql >/dev/null 2>&1; then
    eval "$(extract_pg_env)"

    printf '%sChecking if database exists...%s\n' "$YELLOW" "$NC"
    if PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tAc "SELECT 1" >/dev/null 2>&1; then
        printf '%sDatabase %s already exists.%s\n' "$GREEN" "$DB_NAME" "$NC"
    else
        printf '%sCreating database %s...%s\n' "$YELLOW" "$DB_NAME" "$NC"
        # Create by connecting to the maintenance DB ("postgres").
        PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
            -c "CREATE DATABASE \"${DB_NAME}\"" >/dev/null 2>&1 || true
        printf '%sDatabase created!%s\n' "$GREEN" "$NC"
    fi
else
    printf '%sWarning: psql not found. Skipping database existence check.%s\n' "$YELLOW" "$NC"
    printf '%sEnsure the database %s exists on the server.%s\n' "$YELLOW" "$DB_NAME" "$NC"
fi

# Run migrations
printf '%sRunning Alembic migrations...%s\n' "$YELLOW" "$NC"
uv run alembic upgrade head
printf '%sMigrations complete!%s\n' "$GREEN" "$NC"

# Optionally seed data (note: real data is loaded by the historic-refresh cron job)
if [ "${1:-}" = "--seed" ]; then
    printf '%sSeeding initial data...%s\n' "$YELLOW" "$NC"
    printf '%sNote: Historic data will be loaded by the historic-refresh cron job.%s\n' "$YELLOW" "$NC"
    printf '%sYou can trigger it manually via the admin endpoint after deployment.%s\n' "$YELLOW" "$NC"
fi

# Verify tables
if command -v psql >/dev/null 2>&1; then
    printf '%sVerifying database schema...%s\n' "$YELLOW" "$NC"
    eval "$(extract_pg_env)"
    TABLE_COUNT=$(PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
        -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo 0)
    printf '%sFound %s tables in database.%s\n' "$GREEN" "$TABLE_COUNT" "$NC"
fi

printf '%s=== Database setup complete! ===%s\n' "$GREEN" "$NC"
