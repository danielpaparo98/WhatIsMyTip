#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cd "$(dirname "$0")/.."

printf '%sRunning FastAPI unit tests...%s\n' "$YELLOW" "$NC"

uv run pytest tests/unit/ -v "$@"

printf '%sTests complete!%s\n' "$GREEN" "$NC"
