"""FastAPI application entry point.

Phase 1 scaffolding: lifespan, middleware, security, exception handlers,
and the ``/health`` route.  Phase 2 will add routers for games, tips,
backtest, and admin.

Run locally with::

    cd backend
    uv run uvicorn main:app --reload

Production (gunicorn)::

    uv run gunicorn main:app -k uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.exceptions import BackendServiceError
from app.core.lifespan import lifespan
from app.core.middleware import (
    RequestIDMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import get_limiter
from packages.shared.config import settings

# Module-level logger reused by exception handlers
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App construction
# ---------------------------------------------------------------------------

app = FastAPI(
    title="WhatIsMyTip API",
    version="0.1.0",
    description="AFL tipping + analytics API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — must be added FIRST so it handles preflight before our
# custom middleware short-circuits on size limits, etc.
#
# The dev origins (localhost:3000, 127.0.0.1:3000) come from settings
# and are kept in sync via env vars.  Credentials are disabled because
# the API uses ``X-API-Key`` headers, not cookies.
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=600,  # 10 min preflight cache (BACKEND-FAAS-CODE-REVIEW §3.4)
)

# ---------------------------------------------------------------------------
# Custom middleware
# ---------------------------------------------------------------------------
#
# Starlette's ``add_middleware`` wraps the current app, so the LAST
# call is the OUTERMOST layer.  We add the request-ID middleware last
# so every response (including CORS preflight) carries an ID.

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RequestIDMiddleware)  # outermost

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

app.state.limiter = get_limiter()
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(BackendServiceError)
async def backend_error_handler(
    request: Request, exc: BackendServiceError
) -> JSONResponse:
    """Map a :class:`BackendServiceError` to its declared HTTP status."""
    headers = None
    if exc.status_code == 429:
        headers = {"Retry-After": "60"}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": _request_id(request),
        },
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a structured 422 for Pydantic validation errors."""
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "Invalid request",
            "errors": exc.errors(),
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Last-resort handler: log the traceback, return a sanitized 500."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "An internal error occurred",
            "request_id": _request_id(request),
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Phase 1
app.include_router(health_router, tags=["health"])

# Phase 2 — ported from the FaaS handlers; URL paths and response
# field names are preserved 1:1 so the frontend and existing clients
# don't change.
from app.api.games import router as games_router
from app.api.tips import router as tips_router
from app.api.backtest import router as backtest_router
from app.api.admin import router as admin_router

app.include_router(games_router, prefix="/api/games", tags=["games"])
app.include_router(tips_router, prefix="/api/tips", tags=["tips"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["backtest"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
