"""Security tests for ``app.core.lifespan``.

These tests pin the contract that the FastAPI app MUST refuse to
start when it is misconfigured for production:

* ``settings.environment == "production"`` AND
* ``settings.admin_api_key == ""``

is a silent footgun today: ``/api/admin/**`` endpoints would be
unguarded (since ``verify_api_key`` returns ``False`` when the
configured key is empty, but they would be silently reachable in a
"403 - not authorized" state — which is *not* the same as
"unreachable").  Worse, an operator who sets ``ADMIN_API_KEY=`` in
their App Platform spec (a copy-paste error) gets an app that
*starts* but fails every admin call with no log hint as to why.

The fix is to fail fast at startup with a CRITICAL log entry.
Non-production environments (``development``, ``test``, ``staging``)
may still start with an empty key — that matches the local-dev UX
of ``ADMIN_API_KEY=""`` in ``.env.example``.

What we test:

1. ``production`` + empty key  → lifespan raises
2. ``production`` + non-empty key → lifespan starts cleanly
3. ``development`` + empty key → lifespan starts cleanly (with a
   warning logged)

We use ``monkeypatch.setenv`` + a fresh ``Settings`` instance
instead of mutating the global ``settings`` singleton — the lifespan
is free to read either, but the env-var round-trip is what we want
to pin.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.shared.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_shared(monkeypatch, *, db_healthy=True, redis_healthy=True):
    """Patch the DB/Redis factories the lifespan touches (so the test
    isolates on the admin-key check, not on infra availability).
    """
    engine = MagicMock(name="engine")
    engine.dispose = AsyncMock()

    if db_healthy:
        engine.connect = MagicMock(return_value=_async_ctx(None))
    else:
        engine.connect = MagicMock(side_effect=RuntimeError("db down"))

    redis = MagicMock(name="redis")
    redis.aclose = AsyncMock()
    if redis_healthy:
        redis.ping = AsyncMock(return_value=True)
    else:
        redis.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    monkeypatch.setattr("packages.shared.db.get_engine", lambda: engine)
    monkeypatch.setattr(
        "packages.shared.db.dispose_engine",
        lambda force=False: _await(engine.dispose()),
    )
    monkeypatch.setattr("packages.shared.cache._get_client", lambda: redis)
    monkeypatch.setattr(
        "packages.shared.cache.close_redis_pool",
        lambda force=False: _await(redis.aclose()),
    )
    return engine, redis


def _await(coro):
    """Convert an AsyncMock call into a coroutine for ``lambda``."""
    async def _wrapper():
        await coro
    return _wrapper()


def _async_ctx(value):
    """Build a coroutine-returning async-context manager yielding ``value``."""
    class _Ctx:
        async def __aenter__(self):
            return value

        async def __aexit__(self, *_args):
            return None

    return _Ctx()


def _patch_scheduler(monkeypatch):
    """Patch the scheduler init/shutdown helpers (lifespan is robust to
    scheduler failures; we just want it to not blow up here).
    """
    scheduler = MagicMock(name="scheduler")
    scheduler.running = True
    scheduler.get_jobs = MagicMock(return_value=[])

    async def _fake_init(session_factory, *, existing=None):
        return scheduler

    async def _fake_shutdown(sched):
        sched.running = False

    monkeypatch.setattr("app.core.lifespan.init_scheduler", _fake_init)
    monkeypatch.setattr("app.core.lifespan.shutdown_scheduler", _fake_shutdown)
    return scheduler


def _build_settings(monkeypatch, *, environment: str, admin_api_key: str) -> Settings:
    """Construct a fresh ``Settings`` instance with the requested
    environment + admin_api_key.  We use ``model_construct`` to skip
    pydantic's full validation pass — those settings are otherwise
    well-formed — and we patch the env vars the test wants.
    """
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("ADMIN_API_KEY", admin_api_key)
    return Settings()


def _patch_lifespan_settings(monkeypatch, settings: Settings) -> None:
    """Make ``app.core.lifespan`` see ``settings`` for its checks.

    The lifespan uses ``from packages.shared.config import settings``,
    which binds the name in the lifespan module to the singleton at
    import time.  Patching ``packages.shared.config.settings`` is
    therefore not enough — we must patch the symbol in the
    ``app.core.lifespan`` module itself.
    """
    monkeypatch.setattr("app.core.lifespan.settings", settings)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRefuseToStartOnEmptyAdminKeyInProduction:
    """The app MUST refuse to start in production without an admin key."""

    def test_production_empty_admin_key_raises(self, monkeypatch, caplog):
        from app.core.lifespan import lifespan

        _patch_shared(monkeypatch)
        _patch_scheduler(monkeypatch)
        settings = _build_settings(
            monkeypatch, environment="production", admin_api_key=""
        )
        _patch_lifespan_settings(monkeypatch, settings)

        app = FastAPI(lifespan=lifespan)
        with caplog.at_level(logging.CRITICAL, logger="app.core.lifespan"):
            with pytest.raises(RuntimeError) as exc_info:
                with TestClient(app):
                    pass

        # The error message must be actionable — the operator needs
        # to know which env var to set.
        msg = str(exc_info.value)
        assert "ADMIN_API_KEY" in msg, (
            f"Startup error should mention ADMIN_API_KEY; got: {msg!r}"
        )
        # We must have emitted a CRITICAL log entry — operators rely
        # on this in log drains / alerting.
        criticals = [
            r for r in caplog.records
            if r.levelno >= logging.CRITICAL
            and r.name == "app.core.lifespan"
        ]
        assert criticals, (
            "Expected a CRITICAL log entry from app.core.lifespan when "
            "the app refuses to start due to missing ADMIN_API_KEY."
        )

    def test_production_valid_admin_key_starts(self, monkeypatch):
        from app.core.lifespan import lifespan

        _patch_shared(monkeypatch)
        _patch_scheduler(monkeypatch)
        settings = _build_settings(
            monkeypatch, environment="production", admin_api_key="not-empty-secret"
        )
        _patch_lifespan_settings(monkeypatch, settings)

        app = FastAPI(lifespan=lifespan)

        @app.get("/")
        def _root():
            return {"ok": True}

        with TestClient(app) as client:
            r = client.get("/")
            assert r.status_code == 200
            assert r.json() == {"ok": True}

    def test_development_empty_admin_key_starts_with_warning(self, monkeypatch, caplog):
        """Empty key is allowed in non-prod (local-dev UX) — but logged."""
        from app.core.lifespan import lifespan

        _patch_shared(monkeypatch)
        _patch_scheduler(monkeypatch)
        settings = _build_settings(
            monkeypatch, environment="development", admin_api_key=""
        )
        _patch_lifespan_settings(monkeypatch, settings)

        app = FastAPI(lifespan=lifespan)

        @app.get("/")
        def _root():
            return {"ok": True}

        with caplog.at_level(logging.WARNING, logger="app.core.lifespan"):
            with TestClient(app) as client:
                r = client.get("/")
                assert r.status_code == 200

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "app.core.lifespan"
            and "ADMIN_API_KEY" in r.getMessage()
        ]
        assert warnings, (
            "Non-prod startup with empty ADMIN_API_KEY should emit a "
            "WARNING log entry so the operator notices the misconfig."
        )

    @pytest.mark.parametrize("environment", ["staging", "test"])
    def test_non_production_environments_skip_check(self, monkeypatch, environment):
        """``staging`` and ``test`` (and any other non-prod) MUST start
        with an empty admin key — the check is a production-only safety net.
        """
        from app.core.lifespan import lifespan

        _patch_shared(monkeypatch)
        _patch_scheduler(monkeypatch)
        settings = _build_settings(
            monkeypatch, environment=environment, admin_api_key=""
        )
        _patch_lifespan_settings(monkeypatch, settings)

        app = FastAPI(lifespan=lifespan)

        @app.get("/")
        def _root():
            return {"ok": True}

        with TestClient(app) as client:
            r = client.get("/")
            assert r.status_code == 200
