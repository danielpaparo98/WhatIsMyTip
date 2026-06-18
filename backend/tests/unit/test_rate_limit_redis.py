"""Tests for SEC-ME-004: the slowapi Limiter must be Redis-backed.

Why this matters
----------------
The Phase 4 ``Limiter`` is constructed without a ``storage_uri``, which
falls back to an in-process counter.  In a multi-worker deployment
(e.g. uvicorn ``--workers 2`` on App Platform) each worker tracks its
own counter, so the effective rate limit per client is
``N_workers * rate_limit_max_requests``.  An attacker can fan out
requests to many workers (or simply by reconnecting / load-balancing
to different workers) to bypass the limit entirely.

Fix
---
Pass ``storage_uri=settings.redis_url`` to ``slowapi.Limiter`` so all
workers share a single counter via Redis (the same Redis we already
use for caching).
"""

from __future__ import annotations

from slowapi import Limiter

from packages.shared.config import settings


class TestLimiterStorage:
    """The Limiter must be backed by Redis for cross-worker aggregation."""

    def test_limiter_uses_redis_storage_uri(self, monkeypatch) -> None:
        from app.core.rate_limit import get_limiter

        # Force a known redis_url so we can assert the Limiter saw it.
        monkeypatch.setattr(settings, "redis_url", "redis://test-redis:6379/0")

        captured: dict = {}
        original_init = Limiter.__init__

        def spy(self, *args, **kwargs):
            captured["storage_uri"] = kwargs.get("storage_uri")
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(Limiter, "__init__", spy)
        get_limiter()

        assert captured["storage_uri"] == "redis://test-redis:6379/0", (
            "Limiter must be configured with `storage_uri=settings.redis_url` "
            "so multi-worker deployments share a single counter."
        )

    def test_limiter_default_storage_uri_is_settings_redis_url(
        self, monkeypatch
    ) -> None:
        """When settings.redis_url is the default, the Limiter should still
        pass it through (not the in-process default)."""
        from app.core.rate_limit import get_limiter

        captured: dict = {}
        original_init = Limiter.__init__

        def spy(self, *args, **kwargs):
            captured["storage_uri"] = kwargs.get("storage_uri")
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(Limiter, "__init__", spy)
        get_limiter()

        # The storage_uri must be a Redis URL (starts with ``redis://``),
        # never the slowapi default of in-memory storage.
        assert captured["storage_uri"] is not None, (
            "Limiter must explicitly set storage_uri (not rely on slowapi's "
            "in-memory default, which breaks under multi-worker deployments)."
        )
        assert captured["storage_uri"].startswith("redis://"), (
            f"Limiter storage_uri must be a redis:// URL; got {captured['storage_uri']!r}"
        )
        # And it must match the configured redis_url.
        assert captured["storage_uri"] == settings.redis_url
