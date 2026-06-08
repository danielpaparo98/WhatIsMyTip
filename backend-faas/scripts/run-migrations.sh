#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Running database migrations...${NC}"

# Change to project root
cd "$(dirname "$0")/.."

# Load environment variables
if [ -f .env ]; then
    set -a
    source <(grep -v '^#' .env | grep -v '^$')
    set +a
fi

# Run migrations
uv run alembic upgrade head

echo -e "${GREEN}Migrations complete!${NC}"
