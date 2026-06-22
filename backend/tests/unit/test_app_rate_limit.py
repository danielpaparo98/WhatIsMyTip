"""Unit tests for ``app.core.rate_limit``.

Covers:
- ``get_limiter()`` returns a configured :class:`slowapi.Limiter`
- The default limit is derived from settings
- ``get_remote_address`` is used (real client IP, not spoofable header)
- The limiter exposes ``.limit()`` for per-route overrides
- The limiter can be attached to a FastAPI app
- A route that exceeds its per-route limit returns 429
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from slowapi import Limiter


def _redis_is_available() -> bool:
    """Probe the configured Redis with a short timeout.

    ``get_limiter()`` builds a ``slowapi.Limiter`` whose ``storage_uri``
    is the project's ``settings.redis_url`` — so the per-route limit
    enforced by ``test_rate_limit_exceeded_returns_429`` increments a
    counter in Redis.  When no Redis is reachable (e.g. a cold
    developer machine, or CI without the Redis service container) the
    counter write raises ``redis.exceptions.ConnectionError`` /
    ``TimeoutError`` and the test fails for an environmental reason
    rather than a code defect.

    This helper pings **the same Redis** the limiter uses — it reads
    ``settings.redis_url`` (no hardcoded ``localhost:6379``) so the
    check honours whatever the environment configures.  The
    connect/socket timeouts are tiny so a missing Redis only delays
    the test by a fraction of a second before it skips.
    """
    import redis
    import redis.exceptions

    from packages.shared.config import settings

    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        client.close()
        return True
    except (
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        OSError,
    ):
        return False


class TestGetLimiter:
    """``get_limiter()`` is a factory that returns a configured Limiter."""

    def test_returns_limiter_instance(self, monkeypatch):
        from app.core.rate_limit import get_limiter

        limiter = get_limiter()
        assert isinstance(limiter, Limiter)

    def test_default_limit_derived_from_settings(self, monkeypatch):
        """The default limit string fed to slowapi reflects settings.

        We inspect the raw string passed to ``Limiter`` by patching the
        constructor and capturing the ``default_limits`` argument.  This
        avoids depending on slowapi's internal ``__str__`` formatting,
        which is lazy and varies between versions.
        """
        from app.core.rate_limit import get_limiter
        from packages.shared.config import settings
        from slowapi import Limiter

        monkeypatch.setattr(settings, "rate_limit_max_requests", 42)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        captured: dict = {}
        original_init = Limiter.__init__

        def spy(self, *args, **kwargs):
            captured["default_limits"] = kwargs.get(
                "default_limits"
            ) or (args[2] if len(args) >= 3 else None)
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(Limiter, "__init__", spy)

        get_limiter()

        assert captured["default_limits"] is not None
        first = captured["default_limits"][0]
        assert "42" in first
        assert "60" in first

    def test_uses_real_remote_address_key_func(self):
        from app.core.rate_limit import get_limiter
        from slowapi.util import get_remote_address

        limiter = get_limiter()
        # The key function should be ``get_remote_address`` from slowapi,
        # not a header-derived function (to prevent client spoofing).
        assert limiter._key_func is get_remote_address

    def test_factory_returns_fresh_instance(self):
        """Each call returns a new Limiter (allows per-test isolation)."""
        from app.core.rate_limit import get_limiter

        a = get_limiter()
        b = get_limiter()
        # Different instances
        assert a is not b

    def test_limiter_can_decorate_route(self):
        from app.core.rate_limit import get_limiter

        limiter = get_limiter()

        @limiter.limit("5/minute")
        def _endpoint(request: Request):  # slowapi requires a request arg
            return "ok"

        # The decorator should attach slowapi metadata to the function.
        assert hasattr(_endpoint, "__wrapped__")


class TestLimiterIntegration:
    """The limiter can be attached to a FastAPI app via ``app.state.limiter``."""

    def test_app_state_limiter(self):
        from app.core.rate_limit import get_limiter

        app = FastAPI()
        app.state.limiter = get_limiter()

        @app.get("/")
        def _r():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_rate_limit_exceeded_returns_429(self, monkeypatch):
        """A 6th request within the window returns 429."""
        if not _redis_is_available():
            pytest.skip(
                "Redis unavailable — skipping rate-limit integration test"
            )
        from app.core.rate_limit import get_limiter
        from packages.shared.config import settings
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        # Force a tight per-route limit by patching settings
        monkeypatch.setattr(settings, "rate_limit_max_requests", 5)
        monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

        app = FastAPI()
        limiter = get_limiter()
        app.state.limiter = limiter
        app.add_exception_handler(
            RateLimitExceeded, _rate_limit_exceeded_handler
        )

        @app.get("/limited")
        @limiter.limit("5/minute")
        def _limited(request: Request, response: Response):
            # ``response`` is required so slowapi can inject rate-limit
            # headers (X-RateLimit-Remaining, X-RateLimit-Reset, etc.).
            return {"ok": True}

        client = TestClient(app, base_url="http://testserver")
        # First 5 should pass
        for _ in range(5):
            r = client.get("/limited")
            assert r.status_code == 200
        # 6th should be rate-limited
        r6 = client.get("/limited")
        assert r6.status_code == 429
