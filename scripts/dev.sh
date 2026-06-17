#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# WhatIsMyTip local development helper.
#
# Subcommands
# -----------
#   up       Start the full stack (postgres + redis + api + frontend).
#            Default: `up -d` (detached).  Pass --logs to follow logs.
#   down     Stop the stack (volumes preserved).
#   reset    Stop the stack AND delete the named volumes (full DB wipe).
#   logs     Tail the logs of one or more services.  e.g.
#              ./scripts/dev.sh logs api
#              ./scripts/dev.sh logs api frontend
#   ps       Show the running containers.
#   shell    Open a shell in the api container.
#   config   Validate the docker-compose.yml and print the resolved
#            config.  Does NOT start any containers.
#   validate Same as `config`.
#   psql     Open a psql shell against the dev database.
#   redis    Open a redis-cli shell against the dev cache.
#
# Container runtime
# -----------------
#   Auto-detects docker first, then podman.  Override with
#   WIMT_RUNTIME=docker (or podman) if you have both installed.
#
# Windows / PowerShell users:  use scripts/dev.ps1 instead.
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

# ---- locate script directory and project root ----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ---- colour helpers ------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---- runtime detection ---------------------------------------------
detect_runtime() {
  if [[ -n "${WIMT_RUNTIME:-}" ]]; then
    echo "$WIMT_RUNTIME"
    return
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "docker"
    return
  fi
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    echo "podman"
    return
  fi
  echo ""
}

RUNTIME="$(detect_runtime)"
if [[ -z "$RUNTIME" ]]; then
  echo -e "${RED}Error: no running container runtime found.${NC}"
  echo "Install Docker Desktop (https://www.docker.com/products/docker-desktop/)"
  echo "or Podman (https://podman.io/docs/installation), then start the daemon."
  echo
  echo "Tip: set WIMT_RUNTIME=docker (or podman) to override auto-detection."
  exit 1
fi

# Pick the compose subcommand for the detected runtime.
if [[ "$RUNTIME" == "docker" ]]; then
  COMPOSE=(docker compose)
else
  # `podman compose` is a plugin; fall back to `podman-compose` if needed.
  if podman help compose >/dev/null 2>&1; then
    COMPOSE=(podman compose)
  elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=(podman-compose)
  else
    echo -e "${RED}Error: podman found but the compose plugin is not installed.${NC}"
    echo "Install it with:  pipx install podman-compose"
    echo "Or:               dnf install podman-compose  (Fedora/RHEL)"
    exit 1
  fi
fi

print_banner() {
  echo -e "${CYAN}WhatIsMyTip dev  ·  runtime=${RUNTIME}  ·  compose=${COMPOSE[*]}${NC}"
}

usage() {
  cat <<EOF
Usage: $0 <command> [args...]

Commands:
  up [--logs]    Start the stack (default: detached).  --logs follows logs.
  down           Stop the stack (volumes preserved).
  reset          Stop the stack AND delete the named volumes.
  logs <svc...>  Tail logs for the given service(s).
  ps             Show running containers.
  shell [svc]    Open a shell in the given service (default: api).
  psql           Open a psql shell against the dev database.
  redis          Open a redis-cli shell against the dev cache.
  config         Validate docker-compose.yml and print the resolved config.

Environment:
  WIMT_RUNTIME   Force runtime to "docker" or "podman".
EOF
}

# ---- subcommands ---------------------------------------------------
cmd_up() {
  print_banner
  local follow_logs=0
  for arg in "$@"; do
    case "$arg" in
      --logs|-f) follow_logs=1 ;;
      *) ;;
    esac
  done
  echo -e "${YELLOW}Starting stack...${NC}"
  "${COMPOSE[@]}" up -d --build
  echo
  echo -e "${GREEN}Stack up.${NC}"
  echo -e "  API:       ${CYAN}http://localhost:8000${NC}  (Swagger UI at /docs)"
  echo -e "  Frontend:  ${CYAN}http://localhost:3000${NC}"
  echo -e "  Postgres:  localhost:5432  (user: wimt, db: whatismytip, pw: wimt_dev_password)"
  echo -e "  Redis:     localhost:6379"
  echo
  if [[ "$follow_logs" == "1" ]]; then
    "${COMPOSE[@]}" logs -f --tail=100
  fi
}

cmd_down() {
  print_banner
  "${COMPOSE[@]}" down
  echo -e "${GREEN}Stack stopped (volumes preserved).${NC}"
}

cmd_reset() {
  print_banner
  echo -e "${YELLOW}This will DELETE the database volume.${NC}"
  read -rp "Continue? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
  "${COMPOSE[@]}" down -v
  echo -e "${GREEN}Stack stopped and volumes deleted.${NC}"
}

cmd_logs() {
  print_banner
  if [[ $# -eq 0 ]]; then
    "${COMPOSE[@]}" logs -f --tail=100
  else
    "${COMPOSE[@]}" logs -f --tail=100 "$@"
  fi
}

cmd_ps() {
  print_banner
  "${COMPOSE[@]}" ps
}

cmd_shell() {
  print_banner
  local svc="${1:-api}"
  "${COMPOSE[@]}" exec "$svc" /bin/bash
}

cmd_config() {
  print_banner
  "${COMPOSE[@]}" config
}

cmd_psql() {
  print_banner
  "${COMPOSE[@]}" exec postgres psql -U wimt -d whatismytip
}

cmd_redis() {
  print_banner
  "${COMPOSE[@]}" exec redis redis-cli
}

# ---- main ----------------------------------------------------------
subcommand="${1:-}"
shift || true

case "$subcommand" in
  up)        cmd_up "$@" ;;
  down)      cmd_down ;;
  reset)     cmd_reset ;;
  logs)      cmd_logs "$@" ;;
  ps)        cmd_ps ;;
  shell|sh)  cmd_shell "$@" ;;
  psql)      cmd_psql ;;
  redis)     cmd_redis ;;
  config|validate) cmd_config ;;
  -h|--help|help|"") usage ;;
  *)
    echo -e "${RED}Unknown command: $subcommand${NC}"
    usage
    exit 1
    ;;
esac
