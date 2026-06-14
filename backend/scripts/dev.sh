#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}=== WhatIsMyTip Local Development ===${NC}"

# Change to project root
cd "$(dirname "$0")/.."

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker not installed${NC}"
    echo "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo -e "${RED}Error: Docker daemon not running${NC}"
    echo "Start Docker Desktop and try again."
    exit 1
fi

# Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Updated .env with local Docker settings.${NC}"
fi

# Start PostgreSQL container
echo -e "${YELLOW}Starting PostgreSQL...${NC}"
docker run -d \
    --name whatismytip-postgres \
    -e POSTGRES_USER=whatismytip \
    -e POSTGRES_PASSWORD=whatismytip \
    -e POSTGRES_DB=whatismytip \
    -p 5432:5432 \
    postgres:16-alpine \
    2>/dev/null || docker start whatismytip-postgres 2>/dev/null || true

# Start Redis container
echo -e "${YELLOW}Starting Redis...${NC}"
docker run -d \
    --name whatismytip-redis \
    -p 6379:6379 \
    redis:7-alpine \
    2>/dev/null || docker start whatismytip-redis 2>/dev/null || true

# Wait for services to be ready
echo -e "${YELLOW}Waiting for services...${NC}"
sleep 3

# Update .env with local Docker URLs if needed
if grep -q "localhost:5432" .env 2>/dev/null; then
    echo -e "${GREEN}Database URL already pointing to localhost.${NC}"
else
    echo -e "${YELLOW}Note: Make sure your .env has the following settings:${NC}"
fi

echo ""
echo -e "${CYAN}=== Local Development Configuration ===${NC}"
echo ""
echo -e "  ${CYAN}PostgreSQL:${NC} postgresql+asyncpg://whatismytip:whatismytip@localhost:5432/whatismytip"
echo -e "  ${CYAN}Redis:${NC}     redis://localhost:6379/0"
echo ""
echo -e "${CYAN}=== Useful Commands ===${NC}"
echo ""
echo -e "  ${GREEN}Run migrations:${NC}    uv run alembic upgrade head"
echo -e "  ${GREEN}Run tests:${NC}         uv run pytest tests/unit/ -v"
echo -e "  ${GREEN}Test a function:${NC}    doctl serverless functions invoke api/games --param body '{}'"
echo -e "  ${GREEN}Stop services:${NC}      docker stop whatismytip-postgres whatismytip-redis"
echo ""
echo -e "${CYAN}=== Function Testing ===${NC}"
echo ""
echo -e "  To test functions locally with doctl:"
echo -e "  1. ${YELLOW}doctl serverless connect${NC}  — connect to your DO Functions namespace"
echo -e "  2. ${YELLOW}doctl serverless deploy .${NC}   — deploy to the connected namespace"
echo -e "  3. Use the function URLs from the deploy output to test endpoints"
echo ""
echo -e "${GREEN}=== Development environment ready! ===${NC}"
