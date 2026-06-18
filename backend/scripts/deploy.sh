#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# deploy.sh — container-based deploy of the WhatIsMyTip FastAPI app.
#
# Phase 4+ retires the FaaS architecture in favour of a single FastAPI
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
#   8. Polls /health until the new revision is up (up to 5 minutes)
#   9. On health-check failure: prints the previous-deployment ID and
#      the rollback command the operator must run.
#
# Required env vars:
#   DO_REGISTRY   e.g. registry.digitalocean.com/whatismytip
#   DO_APP_ID     the App Platform app ID
#
# Optional env vars:
#   IMAGE_TAG     default = current git short SHA
#   SKIP_TESTS    set to 1 to skip the pytest step
#   SKIP_MIGRATE  set to 1 to skip the alembic step
#   APP_URL       default = https://whatismytip.com (the /health poll
#                 target)
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

# ── CRLF self-check (ME-008) ──────────────────────────────────────────
# .gitattributes enforces *.sh text eol=lf, but if a Windows user
# commits with core.autocrlf=true and someone clones with
# core.autocrlf=false, the file lands with CRLF endings and bash
# chokes with `$'\r': command not found`.  Bail loudly instead.
first_byte=$(head -c1 "$0" 2>/dev/null | od -An -c | tr -d ' \n' || true)
if [ "$first_byte" = "\r" ]; then
    printf 'Refusing to run: %s has CRLF line endings.\n' "$0" >&2
    printf 'Run `dos2unix %s` (or `sed -i "s/\\r$//" %s`) and re-commit.\n' "$0" "$0" >&2
    exit 1
fi

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
            printf '%sUnknown argument: %s%s\n' "$RED" "$arg" "$NC" >&2
            exit 1
            ;;
    esac
done

# `run` and `note` wrap commands so --dry-run short-circuits them.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s[dry-run]%s %s\n' "$CYAN" "$NC" "$*"
    else
        printf '%s$ %s%s\n' "$GREEN" "$*" "$NC"
        "$@"
    fi
}
note() { printf '%s▶ %s%s\n' "$YELLOW" "$*" "$NC"; }
ok()   { printf '%s✅ %s%s\n' "$GREEN" "$*" "$NC"; }
die()  { printf '%s❌ %s%s\n' "$RED" "$*" "$NC" >&2; exit 1; }

# ── Cleanup on failure (LO-013) ──────────────────────────────────────
# Failed builds leave dangling `<none>` images that fill the local
# Docker cache.  Clean them up.
cleanup_on_err() {
    local exit_code=$?
    if [ -n "${FULL_IMAGE:-}" ]; then
        docker rmi -f "${FULL_IMAGE}" >/dev/null 2>&1 || true
    fi
    exit "$exit_code"
}
trap cleanup_on_err ERR

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

# ── Registry login validation (HI-004) ───────────────────────────────
# Make sure doctl can reach the target registry BEFORE we spend 5
# minutes building a 700 MB image.
if [ "$DRY_RUN" -eq 0 ]; then
    note "Validating registry login for ${DO_REGISTRY%%/*}…"
    if ! doctl registry login --registry "${DO_REGISTRY%%/*}" >/dev/null 2>&1; then
        die "doctl registry login failed for ${DO_REGISTRY%%/*} — check DIGITALOCEAN_ACCESS_TOKEN"
    fi
    ok "Registry login OK"
fi

# ── Tests ─────────────────────────────────────────────────────────────
if [ "${SKIP_TESTS:-0}" = "1" ]; then
    note "Skipping tests (SKIP_TESTS=1)"
else
    note "Running unit tests…"
    # LO-006: dropped the conflicting `-q` flag (pytest treats it as a
    # synonym of `--quiet` and the output would be ambiguous).
    run uv run pytest tests/unit/ --tb=short
    ok "Tests passed"
fi

# ── Migrations (one-off container) ───────────────────────────────────
if [ "${SKIP_MIGRATE:-0}" = "1" ]; then
    note "Skipping migrations (SKIP_MIGRATE=1)"
else
    note "Running database migrations…"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s[dry-run]%s uv run alembic upgrade head\n' "$CYAN" "$NC"
    else
        uv run alembic upgrade head
        ok "Migrations complete"
    fi
fi

# ── Capture previous deployment ID for rollback reference (CR-004) ─
PREVIOUS_DEPLOYMENT_ID=""
if [ "$DRY_RUN" -eq 0 ]; then
    note "Capturing current deployment ID (for rollback reference)…"
    PREVIOUS_DEPLOYMENT_ID=$(doctl apps list-deployments "${DO_APP_ID}" --format ID --no-header 2>/dev/null | head -n1 || true)
    if [ -n "${PREVIOUS_DEPLOYMENT_ID}" ]; then
        ok "Previous deployment ID: ${PREVIOUS_DEPLOYMENT_ID}"
    else
        note "Could not determine previous deployment ID (this may be the first deploy)"
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

# ── Trigger App Platform deploy (HI-009: rely on unique IMAGE_TAG) ────
# We rely on the unique IMAGE_TAG to force a fresh deployment; this
# lets DO reuse the previous image's build cache for unchanged layers.
note "Triggering App Platform deploy for app ${DO_APP_ID}…"
NEW_DEPLOYMENT_ID=""
if [ "$DRY_RUN" -eq 0 ]; then
    NEW_DEPLOYMENT_ID=$(doctl apps create-deployment "${DO_APP_ID}" --format ID --no-header 2>/dev/null || true)
    if [ -n "${NEW_DEPLOYMENT_ID}" ]; then
        ok "Deployment triggered: ${NEW_DEPLOYMENT_ID}"
    else
        note "Deployment triggered (could not capture new deployment ID)"
    fi
else
    run doctl apps create-deployment "${DO_APP_ID}"
fi

# ── Poll /health (ME-009: longer window) ────────────────────────────
# Best-effort: if the public URL is reachable, poll until it returns
# 2xx.  We give the deploy 5 minutes (App Platform image build +
# container start routinely takes 2-5 minutes).  If /health never
# comes up, we print the rollback command rather than failing the
# script — the deploy itself is async and the operator should make
# the rollback decision.
APP_URL="${APP_URL:-https://whatismytip.com}"
HEALTH_OK=0
if [ "$DRY_RUN" -eq 0 ] && command -v curl >/dev/null 2>&1; then
    note "Polling the public health endpoint (up to 300 s)…"
    for _ in $(seq 1 30); do
        # LO-012: -fsSL fails on non-2xx, follows redirects, silent
        # on progress.
        if curl -fsSL -o /dev/null -w '%{http_code}' "${APP_URL}/health" 2>/dev/null \
            | grep -qE '^(200|503)$'; then
            HEALTH_OK=1
            ok "Service is healthy"
            break
        fi
        sleep 10
    done

    if [ "$HEALTH_OK" -eq 0 ]; then
        note "❗ Service did not become healthy within 300 s."
        note "The deploy itself succeeded — review App Platform logs and decide whether to roll back:"
        if [ -n "${PREVIOUS_DEPLOYMENT_ID}" ]; then
            note "  doctl apps rollback ${DO_APP_ID} --deployment-id ${PREVIOUS_DEPLOYMENT_ID}"
        else
            note "  doctl apps list-deployments ${DO_APP_ID}"
            note "  doctl apps rollback ${DO_APP_ID} --deployment-id <previous-id>"
        fi
        note "New deployment ID (for reference): ${NEW_DEPLOYMENT_ID:-unknown}"
    fi
else
    note "Skipping /health poll (DRY_RUN=1 or curl not available)"
fi

note "=== Deploy complete: ${FULL_IMAGE} ==="
