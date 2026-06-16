#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Smoke test for the local Docker stack configuration.
#
# This script validates that ``docker-compose.yml`` is well-formed and
# that the runtime can be reached, WITHOUT bringing the stack up.  It is
# safe to run in CI / sandboxes where Docker is not installed.
#
# Usage
# -----
#   ./scripts/smoke_local.sh                  # validate only
#   ./scripts/smoke_local.sh --no-up          # same as above
#   ./scripts/smoke_local.sh --up             # also bring the stack up
#   ./scripts/smoke_local.sh --up --health    # also curl /health
#
# Exit codes
# ----------
#   0  all checks passed
#   1  docker compose config failed
#   2  docker compose up failed
#   3  /health endpoint did not respond 200
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

DO_UP=0
DO_HEALTH=0
for arg in "$@"; do
  case "$arg" in
    --up)       DO_UP=1 ;;
    --no-up)    DO_UP=0 ;;
    --health)   DO_HEALTH=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
  esac
done

# --- runtime detection ----------------------------------------------
RUNTIME="${WIMT_RUNTIME:-}"
if [[ -z "$RUNTIME" ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    RUNTIME=docker
  elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    RUNTIME=podman
  fi
fi

if [[ -z "$RUNTIME" ]]; then
  echo -e "${YELLOW}No running container runtime found.${NC}"
  echo "Falling back to YAML syntax check only (no docker compose config)."
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import yaml,sys; d=yaml.safe_load(open('docker-compose.yml')); print('OK: docker-compose.yml is valid YAML'); print('  services:', list(d.get('services', {}).keys())); sys.exit(0)"
  elif command -v python >/dev/null 2>&1; then
    python -c "import yaml,sys; d=yaml.safe_load(open('docker-compose.yml')); print('OK: docker-compose.yml is valid YAML'); print('  services:', list(d.get('services', {}).keys())); sys.exit(0)"
  else
    echo -e "${RED}Cannot validate YAML without Python.${NC}" >&2
    exit 1
  fi
  exit 0
fi

# --- compose subcommand ---------------------------------------------
if [[ "$RUNTIME" == "docker" ]]; then
  COMPOSE=(docker compose)
elif podman help compose >/dev/null 2>&1; then
  COMPOSE=(podman compose)
elif command -v podman-compose >/dev/null 2>&1; then
  COMPOSE=(podman-compose)
else
  echo -e "${RED}podman found but no compose plugin available.${NC}" >&2
  exit 1
fi

echo -e "${CYAN}Smoke test  ·  runtime=$RUNTIME  ·  compose=${COMPOSE[*]}${NC}"
echo

# --- 1) validate compose file ---------------------------------------
echo "[1/3] Validating docker-compose.yml ..."
if ! "${COMPOSE[@]}" config --quiet; then
  echo -e "${RED}FAIL: docker compose config reported an error.${NC}" >&2
  "${COMPOSE[@]}" config || true
  exit 1
fi
echo -e "  ${GREEN}OK${NC}"

# --- 2) optionally bring the stack up -------------------------------
if [[ "$DO_UP" == "1" ]]; then
  echo
  echo "[2/3] Bringing stack up ..."
  if ! "${COMPOSE[@]}" up -d; then
    echo -e "${RED}FAIL: docker compose up failed.${NC}" >&2
    exit 2
  fi
  echo -e "  ${GREEN}OK${NC}"
else
  echo
  echo "[2/3] Skipping stack up (use --up to bring the stack up)"
fi

# --- 3) optionally check /health ------------------------------------
if [[ "$DO_HEALTH" == "1" ]]; then
  echo
  echo "[3/3] Checking API health ..."
  url="http://localhost:8000/health"
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -s -o /dev/null -w "%{http_code}" "$url" || echo "000")
  else
    code="000"
  fi
  if [[ "$code" == "200" ]]; then
    echo -e "  ${GREEN}OK${NC} ($url returned 200)"
  else
    echo -e "  ${RED}FAIL${NC}: $url returned $code (expected 200)"
    exit 3
  fi
else
  echo
  echo "[3/3] Skipping /health check (use --health to check)"
fi

echo
echo -e "${GREEN}All smoke checks passed.${NC}"
