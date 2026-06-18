"""``/health`` endpoint.

Reports the liveness of the database and Redis connections.  Returns 200
in all cases — the body's ``status`` field signals overall health
(``"healthy"`` or ``"degraded"``) so a load balancer can route traffic
accordingly without taking the pod out of rotation for transient
dependency hiccups.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()

# Application version surfaced in the health response.  Kept in sync with
# ``pyproject.toml``'s ``[project] version`` — bumped at release time.
APP_VERSION = "0.1.0"


async def _check_db(engine: Any) -> str:
    """Return ``"ok"`` or ``"error"`` based on a quick DB connection probe."""
    if engine is None:
        return "error"
    try:
        async with engine.connect():
            return "ok"
    except Exception as exc:  # noqa: BLE001 - probe intentionally broad
        logger.warning("DB health check failed: %s", exc)
        return "error"


async def _check_redis(redis_client: Any) -> str:
    """Return ``"ok"`` or ``"error"`` based on a Redis PING."""
    if redis_client is None:
        return "error"
    try:
        await redis_client.ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - probe intentionally broad
        logger.warning("Redis health check failed: %s", exc)
        return "error"


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Report service liveness.

    Returns:
        ``{"status": "healthy" | "degraded", "db": "ok" | "error",
        "redis": "ok" | "error", "version": "x.y.z", "request_id": "..."}``

    The ``request_id`` is taken from ``request.state.request_id`` when the
    :class:`RequestIDMiddleware` is installed, or the string ``"unknown"``
    when it isn't (e.g. in some tests).
    """
    app = request.app
    engine = getattr(app.state, "engine", None)
    redis_client = getattr(app.state, "redis", None)

    db_status = await _check_db(engine)
    redis_status = await _check_redis(redis_client)

    overall = "healthy" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "db": db_status,
        "redis": redis_status,
        "version": APP_VERSION,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }
