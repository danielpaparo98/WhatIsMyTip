#!/bin/bash
set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Change to project root
cd "$(dirname "$0")/.."

echo -e "${YELLOW}Running FaaS backend tests...${NC}"

# Run tests with any additional arguments passed through
uv run pytest tests/unit/ -v "$@"

echo -e "${GREEN}Tests complete!${NC}"
