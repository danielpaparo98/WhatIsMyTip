"""Integration tests for the Admin API (``/api/admin``).

Covers the three declared route groups:

* ``POST /api/admin/{job_name}/trigger``        — trigger one of four
                                                   cron jobs (admin-only)
* ``GET  /api/admin/historic-refresh/progress`` — current historic-refresh
                                                   progress (R4 contract)
* ``GET  /api/admin/metrics``                   — per-job execution metrics

Auth model: every admin endpoint requires ``X-API-Key`` (router-level
``require_admin_key``).  The tests assert that contract first, then
the business-logic contract for each route.

Special case — ``GET /historic-refresh/progress`` (R4 follow-up):

* In-flight row (``status == 'in_progress'``) wins.
* Else most-recent completed/failed row.
* Else 404 ``not_found``.

The seed fixture inserts one completed ``historic_refresh`` row, which
covers the "completed fallback" branch.  The other three branches are
covered by tests that insert their own rows directly into the
testcontainer's ``generation_progress`` table.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from packages.shared.models import GenerationProgress

from tests.integration.conftest import ADMIN_HEADERS


# ---------------------------------------------------------------------------
# GET /api/admin/metrics
# ---------------------------------------------------------------------------


class TestMetricsAuth:
    """``GET /api/admin/metrics`` — auth contract."""

    def test_metrics_without_api_key_returns_401(self, client):
        """No ``X-API-Key`` → 401 ``invalid_api_key``."""
        resp = client.get("/api/admin/metrics")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_metrics_with_wrong_api_key_returns_401(self, client):
        """Wrong key → 401."""
        resp = client.get(
            "/api/admin/metrics",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


class TestMetricsSuccess:
    """``GET /api/admin/metrics`` — happy path."""

    def test_metrics_returns_per_job_metrics(self, client):
        """The response has ``metrics``, ``system``, ``alerting_enabled``."""
        resp = client.get(
            "/api/admin/metrics",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        for key in ("metrics", "system", "alerting_enabled"):
            assert key in body, f"/admin/metrics missing {key!r}"

        # All four known job names are reported (with their default
        # zero-stats payload because some have no executions seeded).
        metrics = body["metrics"]
        for job_name in (
            "daily-sync",
            "historic-refresh",
            "match-completion",
            "tip-generation",
        ):
            assert job_name in metrics, (
                f"metrics payload missing {job_name!r}: {sorted(metrics)}"
            )
            assert "total_runs" in metrics[job_name]

        # ``daily-sync`` has one execution in the seed.
        daily_sync = metrics["daily-sync"]
        assert daily_sync["total_runs"] == 1
        assert daily_sync["successful_runs"] == 1
        assert daily_sync["failed_runs"] == 0
        assert daily_sync["success_rate"] == 1.0
        assert daily_sync["average_duration_seconds"] == 60.0

    def test_metrics_system_info_shape(self, client):
        """``system`` has python_version + platform."""
        resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)
        body = resp.json()
        system = body["system"]
        assert "python_version" in system
        assert "platform" in system

    def test_metrics_alerting_flag_is_bool(self, client):
        """``alerting_enabled`` is a bool."""
        resp = client.get("/api/admin/metrics", headers=ADMIN_HEADERS)
        body = resp.json()
        assert isinstance(body["alerting_enabled"], bool)


# ---------------------------------------------------------------------------
# GET /api/admin/historic-refresh/progress
# ---------------------------------------------------------------------------


class TestHistoricRefreshProgressAuth:
    """``GET /api/admin/historic-refresh/progress`` — auth contract."""

    def test_progress_without_api_key_returns_401(self, client):
        """No key → 401."""
        resp = client.get("/api/admin/historic-refresh/progress")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_progress_with_wrong_api_key_returns_401(self, client):
        """Wrong key → 401."""
        resp = client.get(
            "/api/admin/historic-refresh/progress",
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401


class TestHistoricRefreshProgressNotFound:
    """Branch 4: no rows at all → 404 ``not_found``.

    We use a dedicated DB-override fixture to point at an empty table
    so this test isn't sensitive to the seed row.
    """

    @pytest_asyncio.fixture
    async def empty_progress_db(
        self, engine
    ) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
        """Truncate generation_progress + leave it empty."""
        factory = async_sessionmaker(engine, expire_on_commit=False)
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE generation_progress RESTART IDENTITY"))
        yield factory
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE generation_progress RESTART IDENTITY"))

    @pytest.fixture
    def client_empty_progress(self, empty_progress_db, engine, admin_api_key):
        """A TestClient whose get_db dep yields from the empty table."""
        from packages.shared.config import settings

        settings.admin_api_key = admin_api_key

        from main import app
        from app.core import db_deps

        async def _override():
            async with empty_progress_db() as session:
                yield session

        app.dependency_overrides[db_deps.get_db] = _override
        app.state.engine = engine
        app.state.redis = None
        app.state.scheduler = None

        from fastapi.testclient import TestClient

        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_progress_returns_404_when_no_rows(self, client_empty_progress):
        """No historic-refresh rows → 404 ``not_found`` (R4 branch 4)."""
        resp = client_empty_progress.get(
            "/api/admin/historic-refresh/progress",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body


class TestHistoricRefreshProgressInFlight:
    """Branch 1: an in-flight row wins over older completed rows."""

    @pytest_asyncio.fixture
    async def progress_with_inflight(
        self, seeded_db
    ) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
        """Add one in_progress row alongside the seeded completed row."""
        async with seeded_db() as session:
            # ``season=None`` to match the CRUD's ``season IS NULL``
            # filter — see the conftest seed fixture for context.
            in_flight = GenerationProgress(
                operation_type="historic_refresh",
                season=None,
                total_items=10,
                completed_items=3,
                status="in_progress",
                started_at=datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc),
            )
            session.add(in_flight)
            await session.commit()
        yield seeded_db

    @pytest.fixture
    def client_progress_inflight(self, progress_with_inflight, engine, admin_api_key):
        from packages.shared.config import settings

        settings.admin_api_key = admin_api_key

        from main import app
        from app.core import db_deps

        async def _override():
            async with progress_with_inflight() as session:
                yield session

        app.dependency_overrides[db_deps.get_db] = _override
        app.state.engine = engine
        app.state.redis = None
        app.state.scheduler = None

        from fastapi.testclient import TestClient

        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_progress_returns_in_flight_row_when_present(
        self, client_progress_inflight
    ):
        """The in-flight row wins over the older completed row (R4 branch 1)."""
        resp = client_progress_inflight.get(
            "/api/admin/historic-refresh/progress",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["completed_items"] == 3
        assert body["total_items"] == 10
        assert body["progress_percentage"] == 30.0
        assert body["operation_type"] == "historic_refresh"


class TestHistoricRefreshProgressCompletedFallback:
    """Branch 2: no in-flight → most-recent completed/failed row.

    The seed fixture inserts one completed row, so the default
    ``client`` fixture already covers this branch.
    """

    def test_progress_returns_most_recent_completed(self, client):
        """With seed only: returns the seeded completed row."""
        resp = client.get(
            "/api/admin/historic-refresh/progress",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["total_items"] == 10
        assert body["completed_items"] == 10
        assert body["progress_percentage"] == 100.0
        assert body["operation_type"] == "historic_refresh"

    def test_progress_completed_response_shape(self, client):
        """All declared response keys are present."""
        resp = client.get(
            "/api/admin/historic-refresh/progress",
            headers=ADMIN_HEADERS,
        )
        body = resp.json()
        for key in (
            "progress_id",
            "operation_type",
            "total_items",
            "completed_items",
            "status",
            "started_at",
            "completed_at",
            "error_message",
            "progress_percentage",
        ):
            assert key in body, f"progress body missing {key!r}"


class TestHistoricRefreshProgressFailedWins:
    """Branch 3: no in-flight + multiple finished rows → most-recent wins,
    even if it's ``failed``.
    """

    @pytest_asyncio.fixture
    async def progress_with_failed(
        self, seeded_db
    ) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
        """Append a newer failed row to the seeded completed row."""
        async with seeded_db() as session:
            # ``season=None`` to match the CRUD's ``season IS NULL``
            # filter — see the conftest seed fixture for context.
            failed = GenerationProgress(
                operation_type="historic_refresh",
                season=None,
                total_items=10,
                completed_items=4,
                status="failed",
                started_at=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 3, 1, 0, 5, tzinfo=timezone.utc),
                error_message="simulated failure",
            )
            session.add(failed)
            await session.commit()
        yield seeded_db

    @pytest.fixture
    def client_progress_failed(self, progress_with_failed, engine, admin_api_key):
        from packages.shared.config import settings

        settings.admin_api_key = admin_api_key

        from main import app
        from app.core import db_deps

        async def _override():
            async with progress_with_failed() as session:
                yield session

        app.dependency_overrides[db_deps.get_db] = _override
        app.state.engine = engine
        app.state.redis = None
        app.state.scheduler = None

        from fastapi.testclient import TestClient

        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_progress_returns_most_recent_failed_row(
        self, client_progress_failed
    ):
        """Newer ``failed`` row wins over older ``completed`` row."""
        resp = client_progress_failed.get(
            "/api/admin/historic-refresh/progress",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_message"] == "simulated failure"
        assert body["completed_items"] == 4
        # The failed row has a newer started_at than the seeded completed.
        assert body["progress_percentage"] == 40.0


# ---------------------------------------------------------------------------
# POST /api/admin/{job_name}/trigger
# ---------------------------------------------------------------------------


class TestAdminTriggerAuth:
    """``POST /api/admin/{job_name}/trigger`` — auth contract.

    The router-level ``require_admin_key`` enforces auth before any
    body validation, so the 401 path is the same regardless of the
    job name (or body shape).
    """

    @pytest.mark.parametrize(
        "job_name", ["daily-sync", "tip-generation", "match-completion", "historic-refresh"]
    )
    def test_trigger_without_api_key_returns_401(self, client, job_name):
        """No key → 401 for every valid job name."""
        resp = client.post(f"/api/admin/{job_name}/trigger", json={})
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "job_name", ["daily-sync", "tip-generation", "match-completion", "historic-refresh"]
    )
    def test_trigger_with_wrong_api_key_returns_401(self, client, job_name):
        """Wrong key → 401 for every valid job name."""
        resp = client.post(
            f"/api/admin/{job_name}/trigger",
            json={},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401


class TestAdminTriggerJobNameValidation:
    """``POST /api/admin/{job_name}/trigger`` — ``job_name`` is allow-listed."""

    @pytest.mark.parametrize(
        "bad_name",
        [
            "not-a-real-job",
            "daily_sync",       # underscore instead of dash
            "DAILY-SYNC",       # wrong case
            "drop-table",
            "tip_generation",   # underscore instead of dash
        ],
    )
    def test_trigger_invalid_job_name_returns_422(self, client, bad_name):
        """Unknown job names → 422 ``invalid_job_name``."""
        resp = client.post(
            f"/api/admin/{bad_name}/trigger",
            json={},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_job_name"


class TestAdminTriggerDailySync:
    """``POST /api/admin/daily-sync/trigger`` — happy path (stubbed)."""

    def test_daily_sync_trigger_returns_200(self, client):
        """Happy path: 200 with the documented ``{success, message,
        season, games_created, games_updated, games_skipped, ...}`` shape.
        """
        fake_sync_stats = {
            "games_created": 2,
            "games_updated": 3,
            "games_skipped": 4,
            "total_games": 9,
            "errors": [],
            "duration_seconds": 1.23,
        }

        with patch("app.api.admin.SquiggleClient") as mock_squiggle_cls, \
             patch("app.api.admin.GameSyncService") as mock_sync_cls, \
             patch("app.api.admin.EloModel") as mock_elo:
            mock_squiggle_cls.return_value.close = AsyncMock()
            mock_sync_cls.return_value.sync_games = AsyncMock(return_value=fake_sync_stats)
            mock_elo.update_cache = AsyncMock()

            resp = client.post(
                "/api/admin/daily-sync/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["season"] >= 2026
        assert body["games_created"] == 2
        assert body["games_updated"] == 3
        assert body["games_skipped"] == 4
        assert body["games_failed"] == 0
        assert "duration_seconds" in body

    def test_daily_sync_trigger_with_explicit_season(self, client):
        """``season=2025`` overrides the default current season."""
        with patch("app.api.admin.SquiggleClient") as mock_squiggle_cls, \
             patch("app.api.admin.GameSyncService") as mock_sync_cls, \
             patch("app.api.admin.EloModel") as mock_elo:
            mock_squiggle_cls.return_value.close = AsyncMock()
            mock_sync_cls.return_value.sync_games = AsyncMock(
                return_value={
                    "games_created": 0,
                    "games_updated": 0,
                    "games_skipped": 0,
                    "total_games": 0,
                    "errors": [],
                    "duration_seconds": 0.0,
                }
            )
            mock_elo.update_cache = AsyncMock()

            resp = client.post(
                "/api/admin/daily-sync/trigger",
                json={"season": 2025},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["season"] == 2025


class TestAdminTriggerMatchCompletion:
    """``POST /api/admin/match-completion/trigger`` — happy path (stubbed)."""

    def test_match_completion_trigger_returns_200(self, client):
        """Happy path: 200 with the ``{success, message, games_checked,
        games_completed, games_already_completed, games_not_ready,
        games_failed, duration_seconds, elo_cache_updated}`` shape.
        """
        fake_stats = {
            "games_checked": 5,
            "games_completed": 2,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": [],
            "duration_seconds": 0.8,
        }

        with patch("app.api.admin.SquiggleClient") as mock_squiggle_cls, \
             patch("app.api.admin.MatchCompletionDetectorService") as mock_detector_cls, \
             patch("app.api.admin.EloModel") as mock_elo:
            mock_squiggle_cls.return_value.close = AsyncMock()
            mock_detector_cls.return_value.detect_and_process_completed_matches = AsyncMock(
                return_value=fake_stats
            )
            mock_elo.update_cache = AsyncMock()

            resp = client.post(
                "/api/admin/match-completion/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["games_checked"] == 5
        assert body["games_completed"] == 2
        assert body["games_already_completed"] == 1
        assert body["games_not_ready"] == 2
        assert body["games_failed"] == 0
        # elo_cache_updated is True because games_completed > 0.
        assert body["elo_cache_updated"] is True


class TestAdminTriggerTipGeneration:
    """``POST /api/admin/tip-generation/trigger`` — happy path (stubbed)."""

    def test_tip_generation_trigger_with_season_and_round(self, client):
        """``season`` + ``round_id`` → calls ``generate_for_round``."""
        fake_stats = {
            "message": "Generated 3 tips",
            "season": 2025,
            "round_id": 1,
            "games_processed": 1,
            "tips_created": 3,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 4,
            "model_predictions_updated": 0,
            "errors": [],
            "duration_seconds": 0.5,
        }

        with patch("app.api.admin.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(return_value=fake_stats)

            resp = client.post(
                "/api/admin/tip-generation/trigger",
                json={"season": 2025, "round_id": 1, "regenerate": False},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["games_processed"] == 1
        assert body["tips_created"] == 3

    def test_tip_generation_trigger_without_season_runs_upcoming(self, client):
        """No season → ``generate_for_next_upcoming_round``."""
        fake_stats = {
            "message": "Generated tips for upcoming round",
            "season": 2026,
            "round_id": 5,
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
            "duration_seconds": 1.0,
        }

        with patch("app.api.admin.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_next_upcoming_round = AsyncMock(return_value=fake_stats)

            resp = client.post(
                "/api/admin/tip-generation/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2026
        assert body["round_id"] == 5
        # ``generate_for_next_upcoming_round`` was the entry point.
        mock_service.generate_for_next_upcoming_round.assert_awaited_once()


class TestAdminTriggerHistoricRefresh:
    """``POST /api/admin/historic-refresh/trigger`` — happy path (stubbed)."""

    def test_historic_refresh_trigger_returns_200(self, client):
        """Happy path: 200 with ``{success, message, seasons_processed,
        games_synced, tips_generated, errors, duration_seconds,
        season_stats}`` shape.
        """
        fake_stats = {
            "seasons_processed": 2,
            "games_synced": 198,
            "tips_generated": 1782,
            "errors": [],
            "duration_seconds": 42.5,
            "season_stats": {
                "2024": {"games_synced": 99, "tips_generated": 891},
                "2025": {"games_synced": 99, "tips_generated": 891},
            },
        }

        with patch("app.api.admin.HistoricDataRefreshService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.refresh_from_string = AsyncMock(return_value=fake_stats)

            resp = client.post(
                "/api/admin/historic-refresh/trigger",
                json={},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["seasons_processed"] == 2
        assert body["games_synced"] == 198
        assert body["tips_generated"] == 1782
        assert body["errors"] == []
        assert "season_stats" in body
        assert "2024" in body["season_stats"]
