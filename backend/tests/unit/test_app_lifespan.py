"""Unit tests for ``app.core.lifespan``.

Covers:
- Startup hook creates engine and redis on ``app.state``
- Shutdown hook disposes the engine and closes the redis pool
- State persists across requests inside the lifespan
- Shutdown still runs when a request raises
- Failures during startup do not crash the app (graceful degraded mode)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _patch_shared(monkeypatch, *, db_healthy=True, redis_healthy=True):
    """Patch ``packages.shared.db`` and ``packages.shared.cache`` factories."""
    engine = MagicMock(name="engine")
    engine.dispose = AsyncMock()

    if db_healthy:
        engine_ctx = MagicMock()
        engine_ctx.__aenter__ = AsyncMock(return_value=None)
        engine_ctx.__aexit__ = AsyncMock(return_value=None)
        engine.connect = MagicMock(return_value=engine_ctx)
    else:
        engine.connect = MagicMock(side_effect=RuntimeError("db down"))

    redis = MagicMock(name="redis")
    redis.aclose = AsyncMock()
    if redis_healthy:
        redis.ping = AsyncMock(return_value=True)
    else:
        redis.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    def _fake_get_engine():
        return engine

    async def _fake_dispose_engine(force: bool = False):
        await engine.dispose()

    def _fake_get_redis():
        return redis

    async def _fake_close_redis_pool(force: bool = False):
        await redis.aclose()

    monkeypatch.setattr("packages.shared.db.get_engine", _fake_get_engine)
    monkeypatch.setattr("packages.shared.db.dispose_engine", _fake_dispose_engine)
    monkeypatch.setattr("packages.shared.cache._get_client", _fake_get_redis)
    monkeypatch.setattr(
        "packages.shared.cache.close_redis_pool", _fake_close_redis_pool
    )

    return engine, redis


class TestLifespan:
    """The lifespan context manager wires up shared resources on startup."""

    def test_startup_creates_engine_and_redis(self, monkeypatch):
        from app.core.lifespan import lifespan

        engine, redis = _patch_shared(monkeypatch)

        app = FastAPI(lifespan=lifespan)

        @app.get("/state")
        def _state():
            return {
                "has_engine": hasattr(app.state, "engine"),
                "has_redis": hasattr(app.state, "redis"),
            }

        with TestClient(app) as client:
            r = client.get("/state")
            assert r.status_code == 200
            body = r.json()
            assert body["has_engine"] is True
            assert body["has_redis"] is True
            # State objects are the same instances the lifespan installed.
            assert app.state.engine is engine
            assert app.state.redis is redis

        # Shutdown ran: dispose + aclose called
        engine.dispose.assert_awaited()
        redis.aclose.assert_awaited()

    def test_state_persists_across_request(self, monkeypatch):
        from app.core.lifespan import lifespan

        _patch_shared(monkeypatch)

        app = FastAPI(lifespan=lifespan)

        @app.get("/probe")
        def _probe():
            return {
                "has_engine": hasattr(app.state, "engine"),
                "has_redis": hasattr(app.state, "redis"),
            }

        with TestClient(app) as client:
            for _ in range(3):
                resp = client.get("/probe")
                assert resp.status_code == 200
                body = resp.json()
                assert body["has_engine"] is True
                assert body["has_redis"] is True

    def test_shutdown_runs_even_when_request_fails(self, monkeypatch):
        from app.core.lifespan import lifespan

        engine, redis = _patch_shared(monkeypatch)

        app = FastAPI(lifespan=lifespan)

        @app.get("/boom")
        def _boom():
            raise RuntimeError("kaboom")

        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/boom")
            assert r.status_code == 500

        # Shutdown still ran
        engine.dispose.assert_awaited()
        redis.aclose.assert_awaited()

    def test_startup_failure_does_not_crash_app(self, monkeypatch):
        """If ``get_engine`` raises, the app still comes up (degraded mode)."""
        from app.core.lifespan import lifespan

        def _broken_get_engine():
            raise RuntimeError("db totally down")

        def _broken_get_redis():
            raise RuntimeError("redis totally down")

        monkeypatch.setattr("packages.shared.db.get_engine", _broken_get_engine)
        monkeypatch.setattr(
            "packages.shared.cache._get_client", _broken_get_redis
        )

        app = FastAPI(lifespan=lifespan)

        @app.get("/probe")
        def _probe():
            # State attrs will not be set because the factories raised.
            return {
                "engine": getattr(app.state, "engine", "missing"),
                "redis": getattr(app.state, "redis", "missing"),
            }

        with TestClient(app) as client:
            resp = client.get("/probe")
            assert resp.status_code == 200
            body = resp.json()
            assert body["engine"] == "missing"
            assert body["redis"] == "missing"
