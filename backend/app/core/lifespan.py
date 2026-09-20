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


def _run_startup_migrations() -> None:
    """Apply pending Alembic migrations (sync, blocking — call in a thread).

    DUP-MIG: the Dockerfile CMD also runs ``alembic upgrade head``, but
    the deployed runtime pinned ``RUN_MIGRATIONS_ON_START=false``, which
    left the production schema behind the code (the ``match_reports``
    table never existed and the grand-final report 500'd).  This runs
    inside the app itself so schema parity is guaranteed regardless of
    how the container is launched.

    Alembic is sync and blocking; the caller wraps it in a worker
    thread and only invokes this in production.  ``alembic/env.py``
    reads the URL from ``settings``, so no extra configuration is
    needed beyond the ini path.
    """
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    alembic_cfg = Config(str(ini_path))
    # Silence alembic's own stdout logging config; the app logger reports.
    alembic_cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    # DUP-MIG: don't let alembic's fileConfig(disable_existing_loggers)
    # tear down the application's loggers mid-process (DUP-MIG runs
    # inside the live app, unlike CLI migrations).
    alembic_cfg.attributes["configure_logger"] = False
    command.upgrade(alembic_cfg, "head")


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

    # ----- DUP-MIG: schema parity before anything touches the DB -----
    # PRODUCTION ONLY: this is the drift that bit us (the deployed
    # runtime pinned RUN_MIGRATIONS_ON_START=false, so migration 0008
    # never ran and every match-report query 500'd).  Dev/test have
    # their own migration owners (compose init-data / pytest fixtures)
    # and usually no reachable DB, so the step is skipped there.
    #
    # NOT fail-closed, matching this lifespan's stated philosophy
    # ("startup failures are logged but do not abort the app"): if the
    # DB is unreachable the app is degraded either way, and a genuine
    # migration bug would crash-loop the container while the old image
    # could still serve.  The migration retries on the next restart /
    # deploy; the Dockerfile CMD (RUN_MIGRATIONS_ON_START) keeps its
    # hard-exit as the container-level gate.
    if settings.schema_auto_upgrade and settings.environment == "production":
        import asyncio

        try:
            await asyncio.to_thread(_run_startup_migrations)
            logger.info("Schema auto-upgrade complete (alembic head)")
        except Exception as exc:  # noqa: BLE001 — degraded, not fatal
            logger.critical(
                "Startup migration failed — serving on a possibly stale "
                "schema (will retry on next restart): %s",
                exc,
            )

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
