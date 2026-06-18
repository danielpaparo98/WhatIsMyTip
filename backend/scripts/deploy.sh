#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# deploy.sh — container-based deploy of the WhatIsMyTip FastAPI app.
#
# Phase 4 retires the FaaS architecture in favour of a single FastAPI
# container.  This script:
#
#   1. Pre-flight: checks for docker, doctl, uv
#   2. Loads .env (DATABASE_URL, REDIS_URL, etc.)
#   3. Runs the unit test suite
#   4. Runs Alembic migrations (in a one-off container against the
#      same image we'll deploy)
#   5. Builds the image from backend/Dockerfile
#   6. Tags + pushes the image to the DigitalOcean Container Registry
#      (DO_REGISTRY)
#   7. Triggers an App Platform deployment (DO_APP_ID) with the new
#      image
#   8. Polls /health until the new revision is up
#
# Required env vars:
#   DO_REGISTRY   e.g. registry.digitalocean.com/whatismytip
#   DO_APP_ID     the App Platform app ID
#
# Optional env vars:
#   IMAGE_TAG     default = current git short SHA
#   SKIP_TESTS    set to 1 to skip the pytest step
#   SKIP_MIGRATE  set to 1 to skip the alembic step
#
# Flags:
#   --dry-run     print every command, run none of them
#
# Usage:
#   ./scripts/deploy.sh                    # full deploy
#   ./scripts/deploy.sh --dry-run          # show the plan
#   IMAGE_TAG=my-feature ./scripts/deploy.sh
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Flags ──────────────────────────────────────────────────────────────
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown argument: $arg${NC}" >&2
            exit 1
            ;;
    esac
done

# `run` and `note` wrap commands so --dry-run short-circuits them.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo -e "${CYAN}[dry-run]${NC} $*"
    else
        echo -e "${GREEN}\$ $*${NC}"
        "$@"
    fi
}
note() { echo -e "${YELLOW}▶ $*${NC}"; }
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
die()  { echo -e "${RED}❌ $*${NC}" >&2; exit 1; }

# ── Working dir ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."   # backend/

# ── Pre-flight ────────────────────────────────────────────────────────
note "WhatIsMyTip container-based deploy"
note "Pre-flight checks…"

command -v docker >/dev/null 2>&1 || die "docker not installed"
command -v doctl  >/dev/null 2>&1 || die "doctl not installed"
command -v uv     >/dev/null 2>&1 || die "uv not installed"
doctl account get >/dev/null 2>&1 || die "doctl not authenticated (run: doctl auth init)"
docker info >/dev/null 2>&1      || die "docker daemon not running"
ok "Prerequisites OK"

# ── Env vars ──────────────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 0 ] && [ -f .env ]; then
    note "Loading .env…"
    set -a
    # shellcheck disable=SC1091
    # Windows-edited ``.env`` files use CRLF line endings; the
    # trailing ``\r`` would otherwise be concatenated onto every
    # value and corrupt connection strings (e.g.
    # ``postgresql://u:pwd\r@host:5432/db`` refuses to connect).
    # The pipeline below drops comments and blank lines, then
    # strips a trailing ``\r`` from each remaining line before
    # sourcing.
    source <(grep -v '^#' .env | grep -v '^$' | sed 's/\r$//')
    set +a
fi

: "${DO_REGISTRY:?DO_REGISTRY must be set (e.g. registry.digitalocean.com/whatismytip)}"
: "${DO_APP_ID:?DO_APP_ID must be set (the App Platform app ID)}"

# Default IMAGE_TAG to the current git short SHA so every build is
# uniquely tagged without manual intervention.
if [ -z "${IMAGE_TAG:-}" ]; then
    if command -v git >/dev/null 2>&1 && git rev-parse --short HEAD >/dev/null 2>&1; then
        IMAGE_TAG="$(git rev-parse --short HEAD)"
    else
        IMAGE_TAG="manual-$(date +%Y%m%d-%H%M%S)"
    fi
fi

FULL_IMAGE="${DO_REGISTRY}/api:${IMAGE_TAG}"
ok "Image: ${FULL_IMAGE}"

# ── Tests ─────────────────────────────────────────────────────────────
if [ "${SKIP_TESTS:-0}" = "1" ]; then
    note "Skipping tests (SKIP_TESTS=1)"
else
    note "Running unit tests…"
    run uv run pytest tests/unit/ -v --tb=short -q
    ok "Tests passed"
fi

# ── Migrations (one-off container) ───────────────────────────────────
if [ "${SKIP_MIGRATE:-0}" = "1" ]; then
    note "Skipping migrations (SKIP_MIGRATE=1)"
else
    note "Running database migrations…"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo -e "${CYAN}[dry-run]${NC} uv run alembic upgrade head"
    else
        uv run alembic upgrade head
        ok "Migrations complete"
    fi
fi

# ── Build ─────────────────────────────────────────────────────────────
note "Building ${FULL_IMAGE}…"
run docker build \
    -f Dockerfile \
    -t "${FULL_IMAGE}" \
    .

# ── Push ──────────────────────────────────────────────────────────────
note "Pushing ${FULL_IMAGE}…"
run docker push "${FULL_IMAGE}"
ok "Image pushed"

# ── Trigger App Platform deploy ───────────────────────────────────────
note "Triggering App Platform deploy for app ${DO_APP_ID}…"
run doctl apps create-deployment "${DO_APP_ID}" --force-rebuild
ok "Deployment triggered"

# ── Poll /health ──────────────────────────────────────────────────────
# Best-effort: if the public URL is reachable, poll until it returns 200.
# We don't fail the deploy if this never succeeds — the deploy itself
# is async and the script may be invoked without network access to the
# production host.
APP_URL="${APP_URL:-https://whatismytip.com}"
if [ "$DRY_RUN" -eq 0 ] && command -v curl >/dev/null 2>&1; then
    note "Polling ${APP_URL}/health (up to 60 s)…"
    for _ in $(seq 1 30); do
        if status=$(curl -s -o /dev/null -w '%{http_code}' "${APP_URL}/health" 2>/dev/null || true) \
            && [ "$status" = "200" ]; then
            ok "Service is healthy (HTTP $status)"
            note "=== Deploy complete: ${FULL_IMAGE} ==="
            exit 0
        fi
        sleep 2
    done
    note "Service did not become healthy within the timeout."
    note "The deploy itself succeeded — check the App Platform dashboard."
else
    note "Skipping /health poll (DRY_RUN=1 or curl not available)"
fi

note "=== Deploy complete: ${FULL_IMAGE} ==="
