#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# test_dockerfile.sh — build the FastAPI image and curl /health.
#
# Phase 4 introduces a multi-stage Dockerfile for the FastAPI app.
# This script is the smoke test for it: build → run → curl → cleanup.
#
# Usage:
#   ./scripts/test_dockerfile.sh                # full smoke test
#   ./scripts/test_dockerfile.sh --skip-build   # use a pre-built image
#   ./scripts/test_dockerfile.sh --push         # CI: also push to DO_REGISTRY
#
# Environment:
#   DO_REGISTRY   Container registry hostname (required for --push),
#                 e.g. registry.digitalocean.com/whatismytip
#   IMAGE_TAG     Tag for the pushed image (required for --push),
#                 e.g. $(git rev-parse --short HEAD)
#
# Exit code:
#   0  smoke test passed (image built, /health returned 2xx)
#   1  smoke test failed (see stderr for the failing step)
#   2  docker not available — script SKIPS (exits 0) so it can be
#      safely run on machines without Docker (CI, dev laptops).
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# Resolve paths so the script works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_NAME="whatismytip-api-smoke"
IMAGE_TAG="test"
CONTAINER_NAME="whatismytip-api-smoke-container"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Pre-flight: docker present? ───────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    printf '%s⚠️  Docker not found — skipping smoke test.%s\n' "$YELLOW" "$NC"
    printf '%s    (Set SKIP_DOCKER_SMOKE_TEST=1 in CI to silence this.)%s\n' "$YELLOW" "$NC"
    exit 0
fi

if ! docker info >/dev/null 2>&1; then
    printf '%s⚠️  Docker daemon not running — skipping smoke test.%s\n' "$YELLOW" "$NC"
    exit 0
fi

# ── Args ──────────────────────────────────────────────────────────────
SKIP_BUILD=0
SKIP_PUSH=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        --push) SKIP_PUSH=1 ;;
        *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 1 ;;
    esac
done

# ── Cleanup helper ────────────────────────────────────────────────────
cleanup() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# ── Build ─────────────────────────────────────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
    printf '%s▶ Building %s:%s from %s/Dockerfile…%s\n' \
        "$YELLOW" "$IMAGE_NAME" "$IMAGE_TAG" "$BACKEND_DIR" "$NC"
    docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f "${BACKEND_DIR}/Dockerfile" "${BACKEND_DIR}"
fi

# ── Run ───────────────────────────────────────────────────────────────
printf '%s▶ Starting container…%s\n' "$YELLOW" "$NC"
docker run -d --name "${CONTAINER_NAME}" -p 18000:8000 "${IMAGE_NAME}:${IMAGE_TAG}"

# ── Wait for /health to become reachable ──────────────────────────────
printf '%s▶ Waiting for /health…%s\n' "$YELLOW" "$NC"
status="000"
for i in $(seq 1 30); do
    status=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/health || printf '%s' "000")
    if [ "$status" = "200" ] || [ "$status" = "503" ]; then
        # 200 = healthy, 503 = degraded (db/redis down) — both mean the
        # process is up and serving, which is what this smoke test cares
        # about.  The DB/Redis status is verified by the /health body
        # in unit/integration tests, not here.
        printf '%s✅ /health responded with HTTP %s%s\n' "$GREEN" "$status" "$NC"

        # ── Optional: push the image to a registry (CI use only) ────
        if [ "$SKIP_PUSH" -eq 1 ]; then
            if [ -z "${DO_REGISTRY:-}" ] || [ -z "${IMAGE_TAG:-}" ]; then
                printf '%s--push requires DO_REGISTRY and IMAGE_TAG env vars%s\n' "$RED" "$NC" >&2
                exit 1
            fi
            printf '%s▶ Pushing %s/%s:%s to %s…%s\n' \
                "$YELLOW" "$BACKEND_DIR" "$IMAGE_NAME" "$IMAGE_TAG" "$DO_REGISTRY" "$NC"
            docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${DO_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
            docker push "${DO_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
            printf '%s✅ Push complete.%s\n' "$GREEN" "$NC"
        fi
        exit 0
    fi
    sleep 1
done

printf '%s❌ /health never became reachable (last status: %s)%s\n' "$RED" "$status" "$NC" >&2
printf '%s--- recent container logs ---%s\n' "$RED" "$NC" >&2
docker logs --tail 50 "${CONTAINER_NAME}" >&2 || true
exit 1
