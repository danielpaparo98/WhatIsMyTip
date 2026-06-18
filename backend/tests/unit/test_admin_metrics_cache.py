"""Unit tests for ``GET /api/admin/metrics`` caching (HI-006).

These tests pin the contract that the admin metrics endpoint:

1. Caches the response in Redis under a per-job key, so the 7
   per-job SQL queries don't re-run on every request.
2. On a cache hit, the CRUD is **not** consulted at all (the
   cached payload is returned as-is).
3. On a cache miss, the CRUD is consulted once, then the result
   is stored with the expected TTL.

The cache is mocked via ``patch`` so no real Redis is required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import ALLOWED_JOB_NAMES, router as admin_router


ADMIN_HEADERS = {"X-API-Key": "test-api-key"}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    return app


def _override_db(app: FastAPI, session: AsyncSession) -> None:
    from app.core.db_deps import get_db

    async def _override() -> AsyncSession:
        return session

    app.dependency_overrides[get_db] = _override


def _make_metrics(job_name: str) -> dict[str, Any]:
    return {
        "job_name": job_name,
        "total_runs": 10,
        "successful_runs": 9,
        "failed_runs": 1,
        "average_duration_seconds": 5.0,
        "last_run_at": "2025-01-01",
        "last_success_at": "2025-01-01",
        "last_failure_at": "2025-01-02",
        "success_rate": 0.9,
    }


class TestAdminMetricsCache:
    """HI-006: ``GET /api/admin/metrics`` response is cached in Redis.

    On a hit, the per-job CRUD is NOT consulted.  On a miss, the
    CRUD is consulted exactly once per job and the result is
    written to the cache with a positive TTL.
    """

    def test_metrics_response_is_cached_in_redis(self):
        """First request writes the assembled response to Redis with a TTL."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app()
        _override_db(app, mock_session)

        cache_storage: dict[str, str] = {}

        async def _fake_set(key: str, value: str, ttl: Any = None) -> None:
            cache_storage[key] = value
            return None

        async def _fake_get(key: str) -> str | None:
            return cache_storage.get(key)

        async def _fake_get_job_metrics(job_name: str) -> dict[str, Any]:
            return _make_metrics(job_name)

        with patch("app.api.admin.JobExecutionCRUD") as mock_crud_cls, \
             patch("app.api.admin.short_cache") as mock_cache:
            mock_crud_cls.return_value.get_job_metrics = AsyncMock(
                side_effect=_fake_get_job_metrics
            )
            mock_cache.get = AsyncMock(side_effect=_fake_get)
            mock_cache.set = AsyncMock(side_effect=_fake_set)

            client = TestClient(app)
            resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        # One set per job, all with a positive TTL.
        assert mock_cache.set.await_count == len(ALLOWED_JOB_NAMES)
        for call in mock_cache.set.await_args_list:
            args, kwargs = call.args, call.kwargs
            # set(key, value, ttl=...) — ttl may be passed either
            # positionally or as a keyword.
            ttl = kwargs.get("ttl")
            if ttl is None and len(args) > 2:
                ttl = args[2]
            assert ttl is not None and ttl > 0, (
                "metrics cache entries must have a positive TTL"
            )

    def test_metrics_cache_hit_skips_crud(self):
        """On a cache hit the CRUD is NOT consulted."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app()
        _override_db(app, mock_session)

        preloaded = {
            name: _make_metrics(name) for name in ALLOWED_JOB_NAMES
        }
        import json

        # Real RedisCache.get does json.loads on the raw value, so
        # the mock should do the same: preload with JSON-encoded
        # strings and return the decoded dict on ``get``.
        cache_storage = {
            f"admin_metrics:{name}": json.dumps(preloaded[name])
            for name in ALLOWED_JOB_NAMES
        }

        async def _fake_get(key: str) -> str | None:
            raw = cache_storage.get(key)
            return json.loads(raw) if raw is not None else None

        crud_consulted: list[str] = []

        async def _fake_get_job_metrics(job_name: str) -> dict[str, Any]:
            crud_consulted.append(job_name)
            return _make_metrics(job_name)

        with patch("app.api.admin.JobExecutionCRUD") as mock_crud_cls, \
             patch("app.api.admin.short_cache") as mock_cache:
            mock_crud_cls.return_value.get_job_metrics = AsyncMock(
                side_effect=_fake_get_job_metrics
            )
            mock_cache.get = AsyncMock(side_effect=_fake_get)
            mock_cache.set = AsyncMock()

            client = TestClient(app)
            resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        assert resp.json()["metrics"] == preloaded
        assert crud_consulted == [], (
            "CRUD must not be consulted when all per-job cache entries hit"
        )

    def test_metrics_cache_miss_falls_through_to_crud(self):
        """On a cache miss the CRUD is consulted and the result cached."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app()
        _override_db(app, mock_session)

        cache_storage: dict[str, str] = {}

        async def _fake_get(key: str) -> str | None:
            return cache_storage.get(key)

        async def _fake_set(key: str, value: str, ttl: Any = None) -> None:
            cache_storage[key] = value

        async def _fake_get_job_metrics(job_name: str) -> dict[str, Any]:
            return _make_metrics(job_name)

        with patch("app.api.admin.JobExecutionCRUD") as mock_crud_cls, \
             patch("app.api.admin.short_cache") as mock_cache:
            mock_crud_cls.return_value.get_job_metrics = AsyncMock(
                side_effect=_fake_get_job_metrics
            )
            mock_cache.get = AsyncMock(side_effect=_fake_get)
            mock_cache.set = AsyncMock(side_effect=_fake_set)

            client = TestClient(app)
            resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert set(body["metrics"].keys()) == set(ALLOWED_JOB_NAMES)
        # One CRUD call per job; one cache write per job.
        assert mock_crud_cls.return_value.get_job_metrics.await_count == len(
            ALLOWED_JOB_NAMES
        )
        assert mock_cache.set.await_count == len(ALLOWED_JOB_NAMES)

    def test_metrics_cache_unavailable_still_serves(self):
        """If the Redis cache is unavailable (returns ``None``), the
        endpoint still serves fresh data from the CRUD — it just
        doesn't write a cache entry.
        """
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app()
        _override_db(app, mock_session)

        async def _fake_get(key: str) -> str | None:
            return None  # cache always misses (e.g. Redis down)

        async def _fake_set(key: str, value: str, ttl: Any = None) -> None:
            pass

        async def _fake_get_job_metrics(job_name: str) -> dict[str, Any]:
            return _make_metrics(job_name)

        with patch("app.api.admin.JobExecutionCRUD") as mock_crud_cls, \
             patch("app.api.admin.short_cache") as mock_cache:
            mock_crud_cls.return_value.get_job_metrics = AsyncMock(
                side_effect=_fake_get_job_metrics
            )
            mock_cache.get = AsyncMock(side_effect=_fake_get)
            mock_cache.set = AsyncMock(side_effect=_fake_set)

            client = TestClient(app)
            resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert set(body["metrics"].keys()) == set(ALLOWED_JOB_NAMES)