"""Unit tests for the FastAPI Admin router.

All admin endpoints require the ``X-API-Key`` header — the router
applies ``require_admin_key`` at the router level via
``dependencies=[...]``.  These tests assert URL paths, response
shapes, auth, and that the services / CRUD layer is called with
the right arguments.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app_with_admin_router(monkeypatch=None):
    """Build a minimal FastAPI app with the admin router and handlers."""
    from app.api.admin import router
    from app.core.exceptions import BackendServiceError
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(router, prefix="/api/admin")

    @app.exception_handler(BackendServiceError)
    async def _backend_error_handler(_request, exc: BackendServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": "test-request-id",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Invalid request",
                "errors": exc.errors(),
                "request_id": "test-request-id",
            },
        )

    if monkeypatch is not None:
        from packages.shared.config import settings
        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


ADMIN_HEADERS = {"X-API-Key": "the-secret-key"}


# ---------------------------------------------------------------------------
# Path registration
# ---------------------------------------------------------------------------


class TestRouterPaths:
    """The router registers the same paths as the FaaS handler."""

    def test_router_routes_registered(self):
        from app.api.admin import router

        paths = sorted({r.path for r in router.routes})
        # All four triggers + progress + metrics
        assert "/{job_name}/trigger" in paths
        assert "/historic-refresh/progress" in paths
        assert "/metrics" in paths


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAdminAuth:
    """All admin endpoints require ``X-API-Key``."""

    def test_missing_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post("/api/admin/daily-sync/trigger", json={})
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_invalid_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/admin/daily-sync/trigger",
            json={},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_metrics_requires_auth(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/admin/metrics")
        assert resp.status_code == 401

    def test_progress_requires_auth(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/admin/historic-refresh/progress")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /{job_name}/trigger
# ---------------------------------------------------------------------------


class TestAdminTriggers:
    """``POST /api/admin/{job_name}/trigger`` triggers a job."""

    def test_invalid_job_name_returns_422(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/admin/not-a-real-job/trigger",
            json={},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    def test_daily_sync_trigger_success(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_created": 0,
            "games_updated": 3,
            "games_skipped": 6,
            "total_games": 9,
            "errors": [],
            "duration_seconds": 1.5,
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.SquiggleClient") as mock_squiggle_cls, \
             patch("app.api.admin.GameSyncService") as mock_sync_cls, \
             patch("app.api.admin.EloModel") as mock_elo:
            mock_squiggle_cls.return_value.close = AsyncMock()
            mock_sync_cls.return_value.sync_games = AsyncMock(return_value=mock_stats)
            mock_elo.update_cache = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/admin/daily-sync/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["games_updated"] == 3

    def test_match_completion_trigger_success(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_checked": 3,
            "games_completed": 2,
            "games_already_completed": 0,
            "games_not_ready": 1,
            "errors": [],
            "duration_seconds": 0.8,
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.SquiggleClient") as mock_squiggle_cls, \
             patch("app.api.admin.MatchCompletionDetectorService") as mock_detect_cls, \
             patch("app.api.admin.EloModel") as mock_elo:
            mock_squiggle_cls.return_value.close = AsyncMock()
            mock_detect_cls.return_value.detect_and_process_completed_matches = AsyncMock(
                return_value=mock_stats
            )
            mock_elo.update_cache = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/admin/match-completion/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["games_completed"] == 2

    def test_tip_generation_trigger_success(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "message": "Tip generation completed",
            "season": 2025,
            "round_id": 1,
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
            "duration_seconds": 2.5,
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.TipGenerationService") as mock_svc_cls:
            mock_svc_cls.return_value.generate_for_round = AsyncMock(
                return_value=mock_stats
            )
            mock_svc_cls.return_value.generate_for_next_upcoming_round = AsyncMock(
                return_value=mock_stats
            )

            client = TestClient(app)
            resp = client.post(
                "/api/admin/tip-generation/trigger",
                json={"season": 2025, "round_id": 1},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["tips_created"] == 27

    def test_historic_refresh_trigger_success(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "seasons_processed": 1,
            "games_synced": 100,
            "tips_generated": 200,
            "errors": [],
            "duration_seconds": 30.0,
            "season_stats": {},
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.HistoricDataRefreshService") as mock_svc_cls:
            mock_svc_cls.return_value.refresh_from_string = AsyncMock(
                return_value=mock_stats
            )

            client = TestClient(app)
            resp = client.post(
                "/api/admin/historic-refresh/trigger",
                json={"seasons": "2020-2025", "regenerate_tips": False},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["seasons_processed"] == 1


# ---------------------------------------------------------------------------
# GET /historic-refresh/progress
# ---------------------------------------------------------------------------


class TestAdminHistoricRefreshProgress:
    """``GET /api/admin/historic-refresh/progress``."""

    def test_progress_with_active_operation(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_progress = {
            "progress_id": 1,
            "operation_type": "historic_refresh",
            "total_items": 100,
            "completed_items": 75,
            "status": "in_progress",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": None,
            "error_message": None,
            "progress_percentage": 75.0,
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.HistoricDataRefreshService") as mock_svc_cls:
            mock_svc_cls.return_value.get_progress = AsyncMock(
                return_value=mock_progress
            )
            client = TestClient(app)
            resp = client.get(
                "/api/admin/historic-refresh/progress",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["progress_percentage"] == 75.0

    def test_progress_with_no_active_operation(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.HistoricDataRefreshService") as mock_svc_cls:
            mock_svc_cls.return_value.get_progress = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get(
                "/api/admin/historic-refresh/progress",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] is None
        assert body["progress_id"] is None
        assert "message" in body


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


class TestAdminMetrics:
    """``GET /api/admin/metrics``."""

    def test_metrics_returns_per_job_aggregations(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_metrics = {
            "job_name": "daily-sync",
            "total_runs": 10,
            "successful_runs": 9,
            "failed_runs": 1,
            "average_duration_seconds": 5.0,
            "last_run_at": "2025-01-01",
            "last_success_at": "2025-01-01",
            "last_failure_at": "2025-01-02",
            "success_rate": 0.9,
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.JobExecutionCRUD") as mock_crud_cls:
            mock_crud = mock_crud_cls.return_value
            # Return a per-job metrics dict for each call so the
            # ``job_name`` field matches the requested job.
            async def _fake_get_job_metrics(job_name: str) -> dict:
                return {**mock_metrics, "job_name": job_name}

            mock_crud.get_job_metrics = AsyncMock(
                side_effect=_fake_get_job_metrics
            )
            client = TestClient(app)
            resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert "metrics" in body
        assert "system" in body
        assert "alerting_enabled" in body
        # Four job names, all populated
        assert set(body["metrics"].keys()) == {
            "daily-sync",
            "match-completion",
            "tip-generation",
            "historic-refresh",
        }
        for name, metric in body["metrics"].items():
            assert metric["job_name"] == name
