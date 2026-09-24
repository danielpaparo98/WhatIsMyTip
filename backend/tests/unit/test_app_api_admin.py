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
        """GF-OPS: the trigger now returns IMMEDIATELY with a detached
        background run (the job outlives any proxy timeout); progress is
        polled via GET /historic-refresh/progress."""
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
        assert body["success"] is True
        assert body["status"] == "triggered"
        assert body["seasons"] == "2020-2025"

    def test_league_sync_trigger_success(self, monkeypatch):
        """``league-sync`` is a manually-triggerable ALLOWED_JOB_NAME."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "season": 2026,
            "leagues": ["nwfl", "sfl"],
            "leagues_synced": ["nwfl", "sfl"],
            "leagues_failed": [],
            "fixtures_synced": 42,
            "errors": [],
            "results": {},
            "status": "success",
        }

        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch(
            "app.api.admin.run_all_leagues_sync", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = mock_stats

            client = TestClient(app)
            resp = client.post(
                "/api/admin/league-sync/trigger",
                json={"season": 2026, "leagues": ["nwfl", "sfl"]},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["fixtures_synced"] == 42
        assert body["leagues_synced"] == ["nwfl", "sfl"]


# ---------------------------------------------------------------------------
# GET /historic-refresh/progress
# ---------------------------------------------------------------------------


class TestAdminHistoricRefreshProgress:
    """``GET /api/admin/historic-refresh/progress`` contract (R4 follow-up):

    The route returns:
    * **200** with the in-flight row if one exists (most-recent ``in_progress``).
    * **200** with the most-recently-finished row (``completed``/``failed``)
      when no job is currently in flight.
    * **404** when there is no row for this operation.

    The CRUD-level contract (in-flight > completed > None) is covered by
    :mod:`tests.unit.test_generation_progress_crud` (testcontainers-backed).
    These tests pin the **HTTP** contract by stubbing the service layer.
    """

    def test_progress_with_in_progress_operation_returns_200(self, monkeypatch):
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

    def test_progress_with_completed_operation_returns_200(self, monkeypatch):
        """No in-flight row → route returns the most-recently-finished row."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_progress = {
            "progress_id": 2,
            "operation_type": "historic_refresh",
            "total_items": 100,
            "completed_items": 100,
            "status": "completed",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T01:00:00Z",
            "error_message": None,
            "progress_percentage": 100.0,
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
        assert body["status"] == "completed"
        assert body["progress_id"] == 2

    def test_progress_with_failed_operation_returns_200(self, monkeypatch):
        """A ``failed`` row is treated like a completed row in the contract."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_progress = {
            "progress_id": 3,
            "operation_type": "historic_refresh",
            "total_items": 100,
            "completed_items": 42,
            "status": "failed",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:30:00Z",
            "error_message": "Squiggle API unreachable",
            "progress_percentage": 42.0,
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
        assert body["status"] == "failed"
        assert body["error_message"] == "Squiggle API unreachable"

    def test_progress_with_no_operation_returns_404(self, monkeypatch):
        """No row for this operation → 404 ``not_found`` (R4 follow-up)."""
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

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "no historic refresh" in body["message"].lower()


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
        # Five job names, all populated
        assert set(body["metrics"].keys()) == {
            "daily-sync",
            "match-completion",
            "tip-generation",
            "historic-refresh",
            "league-sync",
        }
        for name, metric in body["metrics"].items():
            assert metric["job_name"] == name


# ---------------------------------------------------------------------------
# POST /match-report/regenerate  (REVIEW-MAJOR-2 coverage)
# ---------------------------------------------------------------------------


class TestAdminMatchReportRegenerate:
    """``POST /api/admin/match-report/regenerate`` — a privileged,
    cost-incurring endpoint (LLM spend), so auth + contract are pinned."""

    def test_regenerate_route_registered(self):
        from app.api.admin import router

        paths = {r.path for r in router.routes}
        assert "/match-report/regenerate" in paths

    def test_missing_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post("/api/admin/match-report/regenerate?slug=abc-12345")
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_api_key"

    def test_invalid_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/admin/match-report/regenerate?slug=abc-12345",
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_unknown_slug_returns_404(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.GameCRUD") as mock_game_crud, \
             patch("app.api.admin.MatchReportCRUD") as mock_report_crud, \
             patch("app.api.admin.MatchReportService") as mock_service_cls:
            mock_game_crud.get_by_slug = AsyncMock(return_value=None)
            service = mock_service_cls.return_value
            service.generate_and_store_report = AsyncMock()
            service.close = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/admin/match-report/regenerate?slug=unknown-1",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        # No report row should be touched and no generation attempted.
        mock_report_crud.delete_for_game.assert_not_called()
        service.generate_and_store_report.assert_not_called()

    def test_generated_deletes_existing_before_generating(self, monkeypatch):
        """The delete MUST happen before generation so the service's
        skip-if-exists gate cannot short-circuit the regeneration."""
        from types import SimpleNamespace

        mock_session = AsyncMock(spec=AsyncSession)
        game = SimpleNamespace(id=42)
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        call_order: list[str] = []

        def _record_delete(*_a, **_k):
            call_order.append("delete")

        def _record_generate(*_a, **_k):
            call_order.append("generate")
            return {"ok": True}

        with patch("app.api.admin.GameCRUD") as mock_game_crud, \
             patch("app.api.admin.MatchReportCRUD") as mock_report_crud, \
             patch("app.api.admin.MatchReportService") as mock_service_cls:
            mock_game_crud.get_by_slug = AsyncMock(return_value=game)
            mock_report_crud.delete_for_game = AsyncMock(side_effect=_record_delete)

            service = mock_service_cls.return_value
            service.generate_and_store_report = AsyncMock(side_effect=_record_generate)
            service.close = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/admin/match-report/regenerate?slug=abc-12345",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json() == {"status": "generated"}
        mock_report_crud.delete_for_game.assert_awaited_once_with(mock_session, 42)
        service.generate_and_store_report.assert_awaited_once_with(mock_session, game)
        service.close.assert_awaited_once()
        # Deletion is ordered before generation (skip-if-exists must lose).
        assert call_order == ["delete", "generate"]

    def test_service_none_returns_skipped(self, monkeypatch):
        """When the service declines (non-GF slug, missing key, failure),
        the endpoint reports ``skipped`` rather than erroring."""
        from types import SimpleNamespace

        mock_session = AsyncMock(spec=AsyncSession)
        game = SimpleNamespace(id=7)
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.admin.GameCRUD") as mock_game_crud, \
             patch("app.api.admin.MatchReportCRUD") as mock_report_crud, \
             patch("app.api.admin.MatchReportService") as mock_service_cls:
            mock_game_crud.get_by_slug = AsyncMock(return_value=game)
            mock_report_crud.delete_for_game = AsyncMock()
            service = mock_service_cls.return_value
            service.generate_and_store_report = AsyncMock(return_value=None)
            service.close = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/admin/match-report/regenerate?slug=abc-12345",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert "reason" in body


# ---------------------------------------------------------------------------
# POST /games/{slug}/void-fixture  (DUP-GUARD operator tool)
# ---------------------------------------------------------------------------


class TestAdminVoidFixture:
    """Soft-voids a duplicated fixture row (teams -> NULL, TBC-style)."""

    def _game(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "id": 3706,
            "slug": "p2aa56iknf",
            "squiggle_id": 39880,
            "home_team": "Fremantle",
            "away_team": "Brisbane",
            "completed": False,
            "sync_version": 3,
            "last_synced_at": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_void_route_registered(self):
        from app.api.admin import router

        paths = {r.path for r in router.routes}
        assert "/games/{slug}/void-fixture" in paths

    def test_missing_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post("/api/admin/games/p2aa56iknf/void-fixture")
        assert resp.status_code == 401

    def test_unknown_slug_returns_404(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.admin.GameCRUD") as mock_game_crud:
            mock_game_crud.get_by_slug = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.post(
                "/api/admin/games/unknown-1/void-fixture",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_completed_game_returns_409(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        db = AsyncMock(spec=AsyncSession)
        _override_db(app, db)
        game = self._game(completed=True)

        with patch("app.api.admin.GameCRUD") as mock_game_crud:
            mock_game_crud.get_by_slug = AsyncMock(return_value=game)
            client = TestClient(app)
            resp = client.post(
                "/api/admin/games/p2aa56iknf/void-fixture",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 409
        assert resp.json()["code"] == "game_completed"
        # Historical fact untouched.
        assert game.home_team == "Fremantle"
        assert game.away_team == "Brisbane"

    def test_void_nulls_teams_and_bumps_sync_version(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        db = AsyncMock(spec=AsyncSession)
        _override_db(app, db)
        game = self._game()

        with patch("app.api.admin.GameCRUD") as mock_game_crud:
            mock_game_crud.get_by_slug = AsyncMock(return_value=game)
            client = TestClient(app)
            resp = client.post(
                "/api/admin/games/p2aa56iknf/void-fixture",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "voided"
        assert body["game_id"] == 3706
        assert body["squiggle_id"] == 39880
        assert game.home_team is None
        assert game.away_team is None
        assert game.sync_version == 4
        db.commit.assert_awaited()
        db.refresh.assert_awaited()


# ---------------------------------------------------------------------------
# POST /historic-refresh/reset-progress  (OPS: stale-progress unblock)
# ---------------------------------------------------------------------------


class TestAdminHistoricRefreshResetProgress:
    """Marks stale in_progress historic-refresh rows failed so the next
    trigger stops aborting on the in-progress unique constraint."""

    def _stale_row(self, progress_id: int):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=progress_id,
            operation_type="historic_refresh",
            status="in_progress",
        )

    def test_reset_route_registered(self):
        from app.api.admin import router

        paths = {r.path for r in router.routes}
        assert "/historic-refresh/reset-progress" in paths

    def test_missing_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post("/api/admin/historic-refresh/reset-progress")
        assert resp.status_code == 401

    def test_no_stale_rows_returns_nothing_to_reset(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch(
            "packages.shared.crud.generation_progress.GenerationProgressCRUD.get_in_progress_operations",
            AsyncMock(return_value=[]),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/admin/historic-refresh/reset-progress",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json() == {"status": "nothing_to_reset", "reset": []}

    def test_stale_rows_marked_failed(self, monkeypatch):
        app = _build_app_with_admin_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        stale = [self._stale_row(11), self._stale_row(12)]

        with patch(
            "packages.shared.crud.generation_progress.GenerationProgressCRUD.get_in_progress_operations",
            AsyncMock(return_value=stale),
        ), patch(
            "packages.shared.crud.generation_progress.GenerationProgressCRUD.mark_failed",
            new=AsyncMock(),
        ) as mock_mark:
            client = TestClient(app)
            resp = client.post(
                "/api/admin/historic-refresh/reset-progress",
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "reset"
        assert body["reset"] == [11, 12]
        assert mock_mark.await_count == 2
