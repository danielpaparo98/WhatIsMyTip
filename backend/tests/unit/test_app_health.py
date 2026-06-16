"""Unit tests for ``app.api.health``.

Covers:
- /health returns 200 in the healthy path
- /health returns 200 with ``"degraded"`` when DB or Redis is down
- The response carries ``status``, ``db``, ``redis``, ``version``, ``request_id``
- The request_id is generated when middleware is installed, or falls back
  to ``"unknown"`` when the middleware is not active
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app_with_health():
    """Create a minimal FastAPI app with the health router and a fake state."""
    from app.api.health import router

    app = FastAPI()
    app.include_router(router)

    # Fake state — health endpoint reads from app.state.engine / app.state.redis
    engine = MagicMock()
    redis = MagicMock()
    app.state.engine = engine
    app.state.redis = redis
    return app, engine, redis


def _patch_db_redis_healthy():
    """Patch ``get_engine``/``get_redis`` factories to return healthy mocks."""
    engine = MagicMock()
    # ``engine.connect()`` is an async context manager in real SQLAlchemy,
    # but for the health check we just need a no-op that succeeds.
    engine_ctx = MagicMock()
    engine_ctx.__aenter__ = AsyncMock(return_value=None)
    engine_ctx.__aexit__ = AsyncMock(return_value=None)
    engine.connect = MagicMock(return_value=engine_ctx)

    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)

    return engine, redis


def _patch_db_redis_degraded_db():
    """DB down, Redis up."""
    engine = MagicMock()
    engine.connect = MagicMock(side_effect=RuntimeError("db down"))

    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)

    return engine, redis


def _patch_db_redis_degraded_redis():
    """Redis down, DB up."""
    engine = MagicMock()
    engine_ctx = MagicMock()
    engine_ctx.__aenter__ = AsyncMock(return_value=None)
    engine_ctx.__aexit__ = AsyncMock(return_value=None)
    engine.connect = MagicMock(return_value=engine_ctx)

    redis = MagicMock()
    redis.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    return engine, redis


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """The /health endpoint reports the liveness of DB and Redis."""

    def test_healthy_path(self):
        engine, redis = _patch_db_redis_healthy()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["db"] == "ok"
        assert body["redis"] == "ok"
        assert body["version"] == "0.1.0"

    def test_degraded_when_db_down(self):
        engine, redis = _patch_db_redis_degraded_db()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["db"] == "error"
        assert body["redis"] == "ok"

    def test_degraded_when_redis_down(self):
        engine, redis = _patch_db_redis_degraded_redis()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["db"] == "ok"
        assert body["redis"] == "error"

    def test_includes_version_field(self):
        engine, redis = _patch_db_redis_healthy()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis

        client = TestClient(app)
        resp = client.get("/health")
        body = resp.json()
        # version is a non-empty string
        assert isinstance(body["version"], str)
        assert body["version"]

    def test_includes_request_id_when_middleware_active(self):
        from app.core.middleware import RequestIDMiddleware

        engine, redis = _patch_db_redis_healthy()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis
        app.add_middleware(RequestIDMiddleware)

        client = TestClient(app)
        resp = client.get("/health")
        body = resp.json()
        assert "request_id" in body
        assert len(body["request_id"]) == 36  # UUID4

    def test_request_id_field_present_even_without_middleware(self):
        engine, redis = _patch_db_redis_healthy()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis

        client = TestClient(app)
        resp = client.get("/health")
        body = resp.json()
        # Falls back to a placeholder when middleware isn't installed
        assert "request_id" in body
        assert isinstance(body["request_id"], str)
        assert body["request_id"]  # non-empty

    def test_response_includes_recommended_headers(self):
        from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware

        engine, redis = _patch_db_redis_healthy()
        app, _, _ = _build_app_with_health()
        app.state.engine = engine
        app.state.redis = redis
        app.add_middleware(SecurityHeadersMiddleware)
        app.add_middleware(RequestIDMiddleware)

        client = TestClient(app)
        resp = client.get("/health")
        # Security headers from the middleware
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        # Request ID from the middleware
        assert "X-Request-ID" in resp.headers
