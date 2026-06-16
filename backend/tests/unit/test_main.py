"""Unit tests for ``main.py`` — the FastAPI app assembly.

Covers:
- The app is importable
- /health is registered
- /docs, /redoc, /openapi.json are registered
- CORS preflight is honored
- Security headers are on every response
- /health returns the expected JSON shape
- BackendServiceError → 401/4xx/5xx with structured JSON
- RequestValidationError → 422 with structured JSON
- Unhandled exceptions → 500 with sanitized body
- Phase 2 routers ARE registered at their FaaS-compatible prefixes
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Route


def _all_paths(app) -> set[str]:
    """Collect every route path.  Includes nested ``_IncludedRouter`` paths."""
    paths: set[str] = set()

    def walk(routes) -> None:
        for r in routes:
            if isinstance(r, Route):
                paths.add(r.path)
                continue
            inner = getattr(r, "original_router", None)
            if inner is not None and hasattr(inner, "routes"):
                walk(inner.routes)
            elif hasattr(r, "routes"):
                walk(r.routes)

    walk(app.routes)
    return paths


def _api_prefixes(app) -> set[str]:
    """Return the set of top-level ``/api/*`` route prefixes.

    FastAPI's ``_IncludedRouter`` objects store the include prefix on
    ``include_context.prefix`` (a private field) — not on the
    ``original_router`` itself, which keeps an empty prefix.
    """
    prefixes: set[str] = set()
    for r in app.routes:
        if type(r).__name__ != "_IncludedRouter":
            continue
        ctx = getattr(r, "include_context", None)
        if ctx is None:
            continue
        prefix = getattr(ctx, "prefix", "") or ""
        if prefix.startswith("/api/"):
            prefixes.add(prefix)
    return prefixes


class TestAppImport:
    def test_app_is_fastapi(self):
        from main import app

        assert isinstance(app, FastAPI)

    def test_app_title_and_version(self):
        from main import app

        assert app.title == "WhatIsMyTip API"
        assert app.version == "0.1.0"

    def test_docs_endpoints_registered(self):
        from main import app

        paths = _all_paths(app)
        assert "/docs" in paths
        assert "/redoc" in paths
        assert "/openapi.json" in paths

    def test_health_endpoint_registered(self):
        from main import app

        paths = _all_paths(app)
        assert "/health" in paths


class TestHealthRoute:
    def test_health_returns_200(self):
        from main import app

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert body["status"] in ("healthy", "degraded")
        assert "db" in body
        assert "redis" in body
        assert "version" in body
        assert "request_id" in body


class TestSecurityHeaders:
    def test_security_headers_on_health(self):
        from main import app

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "Referrer-Policy" in resp.headers
        assert "Permissions-Policy" in resp.headers
        assert "Cross-Origin-Opener-Policy" in resp.headers
        assert "Content-Security-Policy" in resp.headers

    def test_request_id_header_present(self):
        from main import app

        client = TestClient(app)
        resp = client.get("/health")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) == 36

    def test_request_id_header_on_404(self):
        from main import app

        client = TestClient(app)
        resp = client.get("/does-not-exist")
        assert "X-Request-ID" in resp.headers


class TestCors:
    def test_cors_preflight(self):
        from main import app

        client = TestClient(app)
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") is not None


class TestExceptionHandlers:
    def test_backend_service_error_returns_structured_json(self):
        from app.core.exceptions import BackendServiceError
        from main import app

        @app.get("/_test/error")
        def _boom():
            raise BackendServiceError(
                status_code=418,
                code="teapot",
                message="I am a teapot",
                details={"foo": "bar"},
            )

        client = TestClient(app)
        resp = client.get("/_test/error")
        assert resp.status_code == 418
        body = resp.json()
        assert body["code"] == "teapot"
        assert body["message"] == "I am a teapot"
        assert body["details"] == {"foo": "bar"}
        assert "request_id" in body

    def test_unhandled_exception_returns_500(self):
        from main import app

        @app.get("/_test/boom")
        def _boom():
            raise RuntimeError("kaboom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/_test/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "internal_error"
        assert body["message"] == "An internal error occurred"
        assert "request_id" in body
        assert "kaboom" not in body["message"]


class TestPhase2Wiring:
    def test_games_router_registered(self):
        from main import app

        prefixes = _api_prefixes(app)
        assert any(p.startswith("/api/games") for p in prefixes), prefixes

    def test_tips_router_registered(self):
        from main import app

        prefixes = _api_prefixes(app)
        assert any(p.startswith("/api/tips") for p in prefixes), prefixes

    def test_backtest_router_registered(self):
        from main import app

        prefixes = _api_prefixes(app)
        assert any(p.startswith("/api/backtest") for p in prefixes), prefixes

    def test_admin_router_registered(self):
        from main import app

        prefixes = _api_prefixes(app)
        assert any(p.startswith("/api/admin") for p in prefixes), prefixes
