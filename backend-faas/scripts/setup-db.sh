#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== WhatIsMyTip Database Setup ===${NC}"

# Change to project root
cd "$(dirname "$0")/.."

# Load environment variables
if [ -f .env ]; then
    echo -e "${YELLOW}Loading .env...${NC}"
    set -a
    source <(grep -v '^#' .env | grep -v '^$')
    set +a
else
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Copy .env.example to .env and configure your settings."
    exit 1
fi

# Check for DATABASE_URL
if [ -z "${DATABASE_URL:-}" ]; then
    echo -e "${RED}Error: DATABASE_URL not set in .env${NC}"
    exit 1
fi

# Extract database name from DATABASE_URL
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:/]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\).*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\).*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')

echo -e "${YELLOW}Database: ${DB_NAME} on ${DB_HOST}:${DB_PORT}${NC}"

# Check if psql is available
if command -v psql &> /dev/null; then
    echo -e "${YELLOW}Checking if database exists...${NC}"

    # Try to connect to the database
    if psql "postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -c "SELECT 1" &> /dev/null; then
        echo -e "${GREEN}Database '${DB_NAME}' already exists.${NC}"
    else
        echo -e "${YELLOW}Creating database '${DB_NAME}'...${NC}"
        psql "postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/postgres" \
            -c "CREATE DATABASE \"${DB_NAME}\";" 2>/dev/null || true
        echo -e "${GREEN}Database created!${NC}"
    fi
else
    echo -e "${YELLOW}Warning: psql not found. Skipping database creation.${NC}"
    echo -e "${YELLOW}Ensure the database '${DB_NAME}' exists on the server.${NC}"
fi

# Run migrations
echo -e "${YELLOW}Running Alembic migrations...${NC}"
uv run alembic upgrade head
echo -e "${GREEN}Migrations complete!${NC}"

# Optionally seed data
if [ "${1:-}" == "--seed" ]; then
    echo -e "${YELLOW}Seeding initial data...${NC}"
    echo -e "${YELLOW}Note: Historic data will be loaded by the historic-refresh cron job.${NC}"
    echo -e "${YELLOW}You can trigger it manually via the admin endpoint after deployment.${NC}"
fi

# Verify tables
echo -e "${YELLOW}Verifying database schema...${NC}"
if command -v psql &> /dev/null; then
    TABLE_COUNT=$(psql "postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}" \
        -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | xargs)
    echo -e "${GREEN}Found ${TABLE_COUNT} tables in database.${NC}"
fi

echo -e "${GREEN}=== Database setup complete! ===${NC}"
