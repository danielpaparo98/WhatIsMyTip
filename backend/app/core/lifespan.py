"""FastAPI lifespan context manager.

Wires up the shared services (SQLAlchemy async engine, Redis pool) on
startup and tears them down on shutdown.  Designed to keep the app
running in a degraded state when the dependencies are unavailable
(e.g. a transient DB outage) so ``/health`` can still report liveness
to the load balancer.

Phase 3 will add the APScheduler init/shutdown hooks (commented out).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

# Use module-level imports (rather than ``from x import y``) so that
# tests can ``monkeypatch.setattr("packages.shared.db.get_engine", ...)``
# and have the patched value picked up at call time.
from packages.shared import cache as _cache
from packages.shared import db as _db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks for the FastAPI app.

    On startup we acquire an async SQLAlchemy engine and a Redis client
    (using the existing lazy singletons in ``packages.shared.*``).  Both
    are stored on ``app.state`` so route handlers can reach them via
    ``request.app.state`` without re-importing the factory.

    On shutdown we dispose the engine and close the Redis pool.  We
    pass ``force=False`` so warm starts can reuse the connection pools
    (matches the FaaS-era policy).

    Failures during startup are logged but do not abort the app — the
    ``/health`` endpoint will report ``"degraded"`` until the
    dependency recovers.
    """
    # ----- Startup -----
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

    # APScheduler init will be added in Phase 3.
    # try:
    #     app.state.scheduler = init_scheduler()
    # except Exception as exc:
    #     logger.warning("Scheduler init failed (degraded mode): %s", exc)

    yield

    # ----- Shutdown -----
    # APScheduler shutdown will be added in Phase 3.
    # if getattr(app.state, "scheduler", None) is not None:
    #     try:
    #         await shutdown_scheduler(app.state.scheduler)
    #     except Exception as exc:
    #         logger.warning("Scheduler shutdown failed: %s", exc)

    try:
        await _cache.close_redis_pool(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis pool close failed: %s", exc)

    try:
        await _db.dispose_engine(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Engine dispose failed: %s", exc)
