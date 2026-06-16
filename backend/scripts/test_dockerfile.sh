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

# ── Pre-flight: docker present? ───────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "⚠️  Docker not found — skipping smoke test."
    echo "    (Set SKIP_DOCKER_SMOKE_TEST=1 in CI to silence this.)"
    exit 0
fi

if ! docker info >/dev/null 2>&1; then
    echo "⚠️  Docker daemon not running — skipping smoke test."
    exit 0
fi

# ── Args ──────────────────────────────────────────────────────────────
SKIP_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
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
    echo "▶ Building ${IMAGE_NAME}:${IMAGE_TAG} from ${BACKEND_DIR}/Dockerfile…"
    docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f "${BACKEND_DIR}/Dockerfile" "${BACKEND_DIR}"
fi

# ── Run ───────────────────────────────────────────────────────────────
echo "▶ Starting container…"
docker run -d --name "${CONTAINER_NAME}" -p 18000:8000 "${IMAGE_NAME}:${IMAGE_TAG}"

# ── Wait for /health to become reachable ──────────────────────────────
echo "▶ Waiting for /health…"
for i in $(seq 1 30); do
    status=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/health || echo "000")
    if [ "$status" = "200" ] || [ "$status" = "503" ]; then
        # 200 = healthy, 503 = degraded (db/redis down) — both mean the
        # process is up and serving, which is what this smoke test cares
        # about.  The DB/Redis status is verified by the /health body
        # in unit/integration tests, not here.
        echo "✅ /health responded with HTTP $status"
        exit 0
    fi
    sleep 1
done

echo "❌ /health never became reachable (last status: $status)" >&2
echo "--- recent container logs ---" >&2
docker logs --tail 50 "${CONTAINER_NAME}" >&2 || true
exit 1
