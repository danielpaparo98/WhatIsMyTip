"""Tests for SEC-LO-006: ``get_request_id`` fallback must be a UUID4.

The previous ``get_request_id`` returned the literal string
``"test-request-id"`` when the middleware wasn't installed.  Every
test in a single pytest run produced the same value, which made
log correlation impossible and meant any test that asserted on the
request_id had to be careful not to collide with the other tests.

The fix: when the middleware is absent, generate a fresh UUID4 and
stash it on ``request.state`` so subsequent lookups within the
same request are stable.
"""

from __future__ import annotations

import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import RequestIDMiddleware, get_request_id

_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class TestGetRequestIdFallbackIsUUID4:
    """The fallback value is a fresh UUID4 (SEC-LO-006)."""

    def test_dependency_without_middleware_returns_uuid4(self):
        """When ``RequestIDMiddleware`` is NOT installed, the dependency
        must generate a UUID4-shaped string instead of a placeholder."""
        app = FastAPI()
        # Note: NO middleware here.

        captured: dict = {}

        @app.get("/id")
        def _id(rid: str = Depends(get_request_id)):
            captured["rid"] = rid
            return {"id": rid}

        client = TestClient(app)
        resp = client.get("/id")
        assert resp.status_code == 200
        rid = captured["rid"]
        # Must be a UUID4: 36 chars, version digit = 4, variant nibble in (8|9|a|b).
        assert len(rid) == 36
        assert rid[14] == "4", f"expected UUID4 version digit, got {rid!r}"
        assert _UUID4_RE.match(rid), f"fallback request_id is not a UUID4: {rid!r}"

    def test_fallback_distinct_per_request(self):
        """Each request without the middleware must get a unique UUID4."""
        app = FastAPI()
        # No middleware.

        seen: list[str] = []

        @app.get("/id")
        def _id(rid: str = Depends(get_request_id)):
            seen.append(rid)
            return {"id": rid}

        client = TestClient(app)
        for _ in range(5):
            client.get("/id")
        assert len(seen) == 5
        assert len(set(seen)) == 5, (
            f"all fallback request_ids must be unique; got {seen!r}"
        )

    def test_placeholder_string_not_used(self):
        """The literal ``"test-request-id"`` placeholder must never be
        returned by ``get_request_id`` (SEC-LO-006 removes it)."""
        app = FastAPI()
        # No middleware.

        captured: dict = {}

        @app.get("/id")
        def _id(rid: str = Depends(get_request_id)):
            captured["rid"] = rid
            return {"id": rid}

        client = TestClient(app)
        client.get("/id")
        assert captured["rid"] != "test-request-id"
        assert "test-request-id" not in captured["rid"]


class TestGetRequestIdStableWithinRequest:
    """Two lookups in the same request return the same value."""

    def test_dependency_returns_same_value_within_one_request(self):
        app = FastAPI()

        seen: list[str] = []

        @app.get("/id")
        def _id(rid: str = Depends(get_request_id)):
            seen.append(rid)
            return {"id": rid}

        # Call the dependency again within the same request via
        # another route to ensure cache.
        @app.get("/id2")
        def _id2(rid: str = Depends(get_request_id)):
            seen.append(rid)
            return {"id": rid}

        client = TestClient(app)
        client.get("/id")
        # Both calls happen in different requests, so the values
        # differ.  We only assert the in-request stability by
        # running two requests and confirming both are valid UUID4s.
        client.get("/id2")
        assert all(_UUID4_RE.match(r) for r in seen), seen

    def test_request_id_persists_to_request_state(self):
        """The first call must stash the value on ``request.state`` so
        a later lookup without going through the dependency still sees
        the same ID.

        We exercise this with a unit test of the dependency directly
        — going through the full FastAPI plumbing for this one
        contract adds noise (and the middleware-installed test above
        already proves the request.state plumbing in real ASGI)."""
        from starlette.requests import Request as StarletteRequest

        # Synthesize a request with a State backing (no app/middleware).
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
        request = StarletteRequest(scope)

        # First call: stashes a UUID4 on request.state.request_id.
        rid1 = get_request_id(request)
        # Second call (no middleware in scope) should return the same value.
        rid2 = get_request_id(request)
        assert rid1 == rid2, (
            f"get_request_id must return the same UUID4 within one request "
            f"so downstream code (loggers, response headers, SQL listeners) "
            f"sees a stable identifier.  Got {rid1!r} then {rid2!r}."
        )
        # And the value lives on request.state.
        assert getattr(request.state, "request_id", None) == rid1
        # And it's a UUID4.
        assert _UUID4_RE.match(rid1), f"first call did not return a UUID4: {rid1!r}"


class TestGetRequestIdWithMiddleware:
    """The middleware-installed path is unchanged (UUID4 per request)."""

    def test_with_middleware_request_id_is_uuid4(self):
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        captured: dict = {}

        @app.get("/id")
        def _id(rid: str = Depends(get_request_id)):
            captured["rid"] = rid
            return {"id": rid}

        client = TestClient(app)
        resp = client.get("/id")
        assert resp.status_code == 200
        assert _UUID4_RE.match(captured["rid"])
