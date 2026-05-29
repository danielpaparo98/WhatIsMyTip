#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== WhatIsMyTip FaaS Deployment ===${NC}"

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v doctl &> /dev/null; then
    echo -e "${RED}Error: doctl CLI not installed${NC}"
    echo "Install: https://docs.digitalocean.com/reference/doctl/how-to/install/"
    exit 1
fi

if ! doctl account get &> /dev/null; then
    echo -e "${RED}Error: doctl not authenticated${NC}"
    echo "Run: doctl auth init"
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv not installed${NC}"
    echo "Install: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo -e "${GREEN}Prerequisites OK${NC}"

# Change to project root (parent of scripts/)
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

# Run tests
echo -e "${YELLOW}Running tests...${NC}"
uv run pytest tests/unit/ -v --tb=short
if [ $? -ne 0 ]; then
    echo -e "${RED}Tests failed! Aborting deployment.${NC}"
    exit 1
fi
echo -e "${GREEN}Tests passed!${NC}"

# Run database migrations
echo -e "${YELLOW}Running database migrations...${NC}"
uv run alembic upgrade head
echo -e "${GREEN}Migrations complete!${NC}"

# Deploy functions
echo -e "${YELLOW}Deploying functions to Digital Ocean...${NC}"
doctl serverless deploy . --env .env
echo -e "${GREEN}Functions deployed!${NC}"

# Get function URLs
echo -e "${YELLOW}Function URLs:${NC}"
doctl serverless functions list

# Verify deployment
echo -e "${YELLOW}Verifying deployment...${NC}"
FUNCTION_URL=$(doctl serverless functions list --format URL --no-header | head -1)
if [ -n "$FUNCTION_URL" ]; then
    HEALTH_URL="${FUNCTION_URL%/}/games"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || true)
    if [ "$HTTP_STATUS" -eq 200 ] || [ "$HTTP_STATUS" -eq 404 ]; then
        echo -e "${GREEN}Deployment verified! (HTTP $HTTP_STATUS)${NC}"
    else
        echo -e "${YELLOW}Warning: Unexpected response (HTTP $HTTP_STATUS)${NC}"
        echo -e "${YELLOW}The functions may still be starting up.${NC}"
    fi
fi

echo -e "${GREEN}=== Deployment complete! ===${NC}"
