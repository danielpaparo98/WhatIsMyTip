"""FastAPI lifespan context manager.

Wires up the shared services (SQLAlchemy async engine, Redis pool) and
the in-process APScheduler on startup and tears them down on shutdown.

The lifespan is designed to keep the app running in a degraded state
when a dependency is unavailable (e.g. a transient DB outage) so that
``/health`` can still report liveness to the load balancer.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

# Use module-level imports (rather than ``from x import y``) so that
# tests can ``monkeypatch.setattr("packages.shared.db.get_engine", ...)``
# and have the patched value picked up at call time.
from app.core.scheduler import init_scheduler, shutdown_scheduler
from packages.shared import cache as _cache
from packages.shared import db as _db
from packages.shared.config import settings

logger = logging.getLogger(__name__)


def _validate_production_security() -> None:
    """Refuse to start the app in production with missing critical config.

    This is a **defense-in-depth** check that supplements the
    ``model_validator`` in :class:`packages.shared.config.Settings`.
    The validator catches misconfiguration at ``Settings()`` construction
    time, but this function provides a second gate at lifespan startup
    in case the environment changed between import and run.

    Validated in production only:

    - ``DATABASE_URL`` must not be the localhost default (the app would
      silently connect to a non-existent local DB and serve ``"degraded"``
      health responses forever).
    - ``ADMIN_API_KEY`` must be set (every admin endpoint would 403).

    In non-production environments (development, test, staging) we emit
    a WARNING and let the app boot — localhost defaults and empty keys
    are the local-dev defaults in ``.env.example``.
    """
    if settings.environment != "production":
        if not settings.admin_api_key:
            logger.warning(
                "ADMIN_API_KEY is empty in non-production environment "
                "(environment=%r). Admin endpoints will reject every "
                "request until ADMIN_API_KEY is set.",
                settings.environment,
            )
        return

    # Production checks — refuse to start.
    errors: list[str] = []

    _LOCALHOST_DEFAULT = "postgresql+asyncpg://localhost/whatismytip"
    if not settings.database_url or settings.database_url == _LOCALHOST_DEFAULT:
        errors.append(
            "DATABASE_URL is unset or pointing at localhost. "
            "Set DATABASE_URL to the production Postgres DSN."
        )

    if not settings.admin_api_key:
        errors.append(
            "ADMIN_API_KEY is empty. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )

    if errors:
        msg = "Refusing to start in production: " + "; ".join(errors)
        logger.critical(msg)
        raise RuntimeError(msg)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks for the FastAPI app.

    On startup we:

    1. Acquire an async SQLAlchemy engine and a Redis client (using
       the existing lazy singletons in ``packages.shared.*``).  Both
       are stored on ``app.state`` so route handlers can reach them
       via ``request.app.state`` without re-importing the factory.
    2. Start the APScheduler (Phase 3) and store it on ``app.state``.

    On shutdown we:

    1. Stop the scheduler.
    2. Dispose the engine and close the Redis pool.  We pass
       ``force=False`` so warm starts can reuse the connection pools
       (matches the FaaS-era policy).

    Failures during startup are logged but do not abort the app — the
    ``/health`` endpoint will report ``"degraded"`` until the
    dependency recovers.

    SECURITY: the production / missing-``ADMIN_API_KEY`` check is
    NOT a soft failure — it raises ``RuntimeError`` and refuses to
    yield to the application.  See ``_validate_production_security``.
    """
    # ----- Startup -----
    # SECURITY: refuse to start the app in production with a missing
    # admin key.  This MUST run before any other startup work — if
    # we accept the request, the load balancer will route traffic to
    # us and every admin call will silently 403.
    _validate_production_security()

    try:
        engine = _db.get_engine()
        app.state.engine = engine
        logger.info("Database engine ready")
    except Exception as exc:  # noqa: BLE001 - startup probe is intentionally broad
        logger.warning("Database engine init failed (degraded mode): %s", exc)

    try:
        redis = _cache._get_client()
        app.state.redis = redis
        logger.info("Redis pool ready")
    except Exception as exc:  # noqa: BLE001 - startup probe is intentionally broad
        logger.warning("Redis pool init failed (degraded mode): %s", exc)

    # APScheduler (Phase 3) — start the in-process cron jobs.
    try:
        # ``get_session`` is an async-context-manager factory, which is
        # exactly what the BaseJob subclasses expect.
        session_factory = _db.get_session
        scheduler = await init_scheduler(session_factory)
        app.state.scheduler = scheduler
        logger.info("APScheduler started with %s job(s)", len(scheduler.get_jobs()))
    except Exception as exc:  # noqa: BLE001 - startup probe is intentionally broad
        logger.warning("Scheduler init failed (degraded mode): %s", exc)

    yield

    # ----- Shutdown -----
    # Stop the scheduler first so we don't fire jobs while tearing
    # down the engine/redis.
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        try:
            await shutdown_scheduler(scheduler)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduler shutdown failed: %s", exc)

    try:
        await _cache.close_redis_pool(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis pool close failed: %s", exc)

    try:
        await _db.dispose_engine(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Engine dispose failed: %s", exc)
