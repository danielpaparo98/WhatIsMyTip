#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

printf '%sRunning database migrations...%s\n' "$YELLOW" "$NC"

# Change to project root
cd "$(dirname "$0")/.."

# Load environment variables
if [ -f .env ]; then
    set -a
    source <(grep -v '^#' .env | grep -v '^$')
    set +a
fi

# Fail fast with a clear message if DATABASE_URL is not set
# (LO-014) instead of letting alembic raise an opaque error.
: "${DATABASE_URL:?DATABASE_URL must be set (load .env or export it)}"

# Run migrations
uv run alembic upgrade head

printf '%sMigrations complete!%s\n' "$GREEN" "$NC"
