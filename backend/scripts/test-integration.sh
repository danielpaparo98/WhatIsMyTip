#!/bin/bash
# Run integration tests (requires Docker for PostgreSQL + Redis)
#
# Usage:
#   ./scripts/test-integration.sh           # run all integration tests
#   ./scripts/test-integration.sh -k cache  # run only cache tests
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Running integration tests..."
echo "Note: PostgreSQL and Redis must be running (use scripts/dev.sh)"
echo ""

uv run pytest tests/integration/ -v --run-integration "$@"
