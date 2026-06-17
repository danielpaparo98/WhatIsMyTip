"""Integration tests for the auto-mounted + ``/health`` endpoints.

Covers:

* ``GET /openapi.json``  — auto-mounted by FastAPI
* ``GET /docs``          — auto-mounted Swagger UI
* ``GET /redoc``         — auto-mounted ReDoc
* ``GET /health``        — declared in :mod:`app.api.health`

The /health endpoint reads ``app.state.engine`` (Postgres) and
``app.state.redis``.  Our testcontainer sets the engine to a working
Podman-Postgres but leaves Redis ``None`` (no Redis container), so the
endpoint reports ``status="degraded"`` and ``redis="error"``.  The
assertions pin that contract.

See ``plans/integration-test-endpoint-inventory.md`` §"Health" for the
authoritative response shape.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Auto-mounted FastAPI routes
# ---------------------------------------------------------------------------


class TestAutoMountedRoutes:
    """FastAPI auto-mounts three routes for the spec / docs."""

    def test_openapi_json_returns_200_with_schema(self, client):
        """``GET /openapi.json`` exposes the OpenAPI 3 schema."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        body = resp.json()
        # OpenAPI 3 schema must have the standard top-level keys.
        for key in ("openapi", "info", "paths"):
            assert key in body, f"openapi.json missing {key!r}"
        # All four router groups are wired.  OpenAPI lists full paths,
        # so the games list endpoint surfaces as ``/api/games/`` (with
        # the trailing slash FastAPI appends to ``router.get("/")``).
        assert "/health" in body["paths"]
        assert "/api/games/" in body["paths"]
        assert "/api/tips/" in body["paths"]
        assert "/api/backtest/" in body["paths"]
        # Admin's first declared route is the {job_name}/trigger POST.
        assert "/api/admin/{job_name}/trigger" in body["paths"]
        assert "/api/admin/historic-refresh/progress" in body["paths"]
        assert "/api/admin/metrics" in body["paths"]

    def test_docs_returns_200_html(self, client):
        """``GET /docs`` serves the Swagger UI HTML."""
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_redoc_returns_200_html(self, client):
        """``GET /redoc`` serves the ReDoc HTML."""
        resp = client.get("/redoc")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    """``GET /health`` reports DB + Redis liveness without taking the
    pod out of rotation (R4 in the inventory).
    """

    def test_health_returns_200_with_required_keys(self, client):
        """``/health`` returns 200 and the contract fields."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("status", "db", "redis", "version", "request_id"):
            assert key in body, f"/health body missing {key!r}"

    def test_health_db_status_is_ok(self, client):
        """``db`` is ``ok`` because the testcontainer Postgres is up."""
        resp = client.get("/health")
        body = resp.json()
        assert body["db"] == "ok", f"expected db=ok, got {body!r}"

    def test_health_redis_status_is_error_without_redis(self, client):
        """``redis`` is ``error`` because the suite has no Redis container.

        Pins the contract that the lifespan / ``app.state.redis=None``
        case degrades gracefully rather than crashing the route.
        """
        resp = client.get("/health")
        body = resp.json()
        assert body["redis"] == "error", f"expected redis=error, got {body!r}"

    def test_health_status_is_degraded_when_redis_unavailable(self, client):
        """Overall ``status`` is ``degraded`` because Redis is not ok."""
        resp = client.get("/health")
        body = resp.json()
        assert body["status"] == "degraded", (
            f"overall status must reflect redis=error, got {body!r}"
        )

    def test_health_returns_request_id_from_middleware(self, client):
        """The response carries a request_id (set by RequestIDMiddleware)."""
        resp = client.get("/health")
        body = resp.json()
        assert body["request_id"], "request_id must be a non-empty string"
        assert isinstance(body["request_id"], str)

    def test_health_reuses_supplied_request_id_header(self, client):
        """If the client supplies X-Request-ID, the response echoes it back."""
        supplied = "trace-abc-12345"
        resp = client.get("/health", headers={"X-Request-ID": supplied})
        assert resp.headers.get("X-Request-ID") == supplied
        assert resp.json()["request_id"] == supplied

    def test_health_version_matches_app_version(self, client):
        """The ``version`` field mirrors the app's declared version."""
        resp = client.get("/health")
        body = resp.json()
        assert body["version"] == "0.1.0", (
            f"unexpected version: {body['version']!r}"
        )
