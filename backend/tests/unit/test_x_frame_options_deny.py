"""Tests for SEC-ME-007: X-Frame-Options alignment to ``DENY``.

The Phase 4 ``SecurityHeadersMiddleware`` already emits
``X-Frame-Options: DENY`` (the only OWASP-recommended value when
embedding the app in a third-party ``<iframe>`` is never a supported
flow).  This test pins that contract so a future contributor can't
"loosen" it back to ``SAMEORIGIN`` (a common web-server default, which would
allow framing by any same-origin page — almost certainly an
oversight on a backend API that serves no HTML).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.middleware import SecurityHeadersMiddleware


class TestXFrameOptionsDenied:
    """The X-Frame-Options header must be exactly ``DENY``."""

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/")
        def _r():
            return {"ok": True}

        @app.get("/boom")
        def _boom():
            raise HTTPException(status_code=404, detail="not found")

        return app

    def test_200_response_sets_deny(self):
        client = TestClient(self._build_app())
        resp = client.get("/")
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_4xx_response_sets_deny(self):
        client = TestClient(self._build_app(), raise_server_exceptions=False)
        resp = client.get("/boom")
        assert resp.status_code == 404
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_post_response_sets_deny(self):
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.post("/echo")
        def _echo(payload: dict):
            return payload

        client = TestClient(app)
        resp = client.post("/echo", json={"k": "v"})
        assert resp.status_code == 200
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_deny_is_not_sameorigin(self):
        """Pin against accidental ``SAMEORIGIN`` (the nginx default)."""
        client = TestClient(self._build_app())
        resp = client.get("/")
        # ``DENY`` is the only OWASP-recommended value for a backend API.
        assert resp.headers["X-Frame-Options"] != "SAMEORIGIN"
        assert resp.headers["X-Frame-Options"] == "DENY"
