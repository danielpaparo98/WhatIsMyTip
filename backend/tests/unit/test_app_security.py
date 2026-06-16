"""Unit tests for ``app.core.security``.

Covers:
- Valid ``X-API-Key`` header returns ``True``
- Invalid key raises ``BackendServiceError(401)``
- Missing header raises ``BackendServiceError(401)``
- Empty header raises ``BackendServiceError(401)``
- Comparison uses :func:`secrets.compare_digest` (timing-attack resistant)
- ``require_admin_key`` is a working FastAPI dependency usable via ``Depends``
"""

from __future__ import annotations

import secrets
import timeit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import BackendServiceError
from packages.shared.config import settings


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestVerifyApiKey:
    """``verify_api_key`` validates the ``X-API-Key`` header."""

    def test_valid_key_returns_true(self, monkeypatch):
        from app.core.security import verify_api_key

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        assert verify_api_key(x_api_key="the-secret-key") is True

    def test_invalid_key_raises_401(self, monkeypatch):
        from app.core.security import verify_api_key

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        with pytest.raises(BackendServiceError) as exc_info:
            verify_api_key(x_api_key="wrong-key")

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_api_key"
        assert exc_info.value.message  # non-empty

    def test_missing_key_raises_401(self, monkeypatch):
        from app.core.security import verify_api_key

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        with pytest.raises(BackendServiceError) as exc_info:
            verify_api_key(x_api_key=None)

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_api_key"

    def test_empty_string_key_raises_401(self, monkeypatch):
        from app.core.security import verify_api_key

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        with pytest.raises(BackendServiceError) as exc_info:
            verify_api_key(x_api_key="")

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_api_key"

    def test_unconfigured_admin_key_raises_401(self, monkeypatch):
        """When ``ADMIN_API_KEY`` is not set, every key must be rejected."""
        from app.core.security import verify_api_key

        monkeypatch.setattr(settings, "admin_api_key", "")

        with pytest.raises(BackendServiceError) as exc_info:
            verify_api_key(x_api_key="any-key-at-all")

        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "invalid_api_key"

    def test_uses_secrets_compare_digest(self, monkeypatch):
        """The check must use ``secrets.compare_digest`` (constant-time)."""
        from app.core import security

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        called_with: list[tuple[str, str]] = []
        original = secrets.compare_digest

        def spy(a: str, b: str) -> bool:  # type: ignore[override]
            called_with.append((a, b))
            return original(a, b)

        monkeypatch.setattr(security.secrets, "compare_digest", spy)

        # Valid call
        security.verify_api_key(x_api_key="the-secret-key")
        # Invalid call
        with pytest.raises(BackendServiceError):
            security.verify_api_key(x_api_key="not-the-key")

        # Both calls must have gone through compare_digest
        assert len(called_with) == 2
        assert all(a == "the-secret-key" or a == "not-the-key" for a, _ in called_with)
        assert all(b == "the-secret-key" for _, b in called_with)

    def test_timing_attack_resistance(self, monkeypatch):
        """The check uses ``compare_digest`` regardless of input length.

        This is a structural test (not a statistical one — those are too
        flaky on shared CI runners).  We assert the implementation calls
        :func:`secrets.compare_digest` for every candidate, and that the
        call receives both the candidate and the secret in full.

        See :mod:`secrets` for the timing-attack-resistant guarantees of
        ``compare_digest``.
        """
        from app.core import security

        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

        # Spy on compare_digest.  We don't care about the return value,
        # only that the function is invoked with the expected arguments.
        call_log: list[tuple[str, str]] = []
        original = security.secrets.compare_digest

        def spy(a, b):  # type: ignore[override]
            call_log.append((a, b))
            return original(a, b)

        monkeypatch.setattr(security.secrets, "compare_digest", spy)

        # Non-matching candidates (matching one is excluded so the call
        # succeeds and we still observe compare_digest on the path).
        wrong_candidates = [
            "the-secret",  # prefix of secret (shorter)
            "the-secret-keyy",  # longer than secret
            "x" * 1000,  # long wrong key
            "",  # empty (short-circuited, never reaches compare_digest)
        ]
        for c in wrong_candidates:
            if c:
                with pytest.raises(BackendServiceError):
                    security.verify_api_key(x_api_key=c)
            else:
                # Empty input is short-circuited before compare_digest.
                with pytest.raises(BackendServiceError):
                    security.verify_api_key(x_api_key=c)

        # Matching candidate: succeeds, but still flows through compare_digest.
        result = security.verify_api_key(x_api_key="the-secret-key")
        assert result is True

        # compare_digest must have been called for every non-empty
        # candidate (including the matching one) with the full secret.
        non_empty = [c for c in wrong_candidates if c]
        non_empty.append("the-secret-key")
        for c in non_empty:
            assert any(a == c and b == "the-secret-key" for a, b in call_log), (
                f"compare_digest not called with ({c!r}, secret)"
            )
        # Empty string is short-circuited — never reaches compare_digest.
        assert not any(a == "" for a, _ in call_log)


# ---------------------------------------------------------------------------
# FastAPI integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def protected_app(monkeypatch):
    """A minimal FastAPI app exposing ``require_admin_key`` as a route guard."""
    from app.core.exceptions import BackendServiceError
    from app.core.security import require_admin_key
    from fastapi.responses import JSONResponse

    monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

    app = FastAPI()

    @app.exception_handler(BackendServiceError)
    async def _backend_error_handler(_request, exc: BackendServiceError):
        # Mirror the handler wired up in ``main.py`` for Phase 1.
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/protected")
    def _protected(_: bool = require_admin_key):
        return {"ok": True}

    return app


class TestRequireAdminKeyDependency:
    """``require_admin_key`` is a FastAPI ``Depends`` that gates protected routes."""

    def test_valid_key_passes(self, protected_app):
        client = TestClient(protected_app)
        resp = client.get("/protected", headers={"X-API-Key": "the-secret-key"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_invalid_key_returns_401(self, protected_app):
        client = TestClient(protected_app)
        resp = client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_missing_header_returns_401(self, protected_app):
        client = TestClient(protected_app)
        resp = client.get("/protected")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_header_lookup_is_case_insensitive(self, protected_app):
        """HTTP headers are case-insensitive; the dep must accept any case."""
        client = TestClient(protected_app)
        resp = client.get("/protected", headers={"x-api-key": "the-secret-key"})
        assert resp.status_code == 200
