"""Unit tests for the FastAPI Backtest router.

The router is a thin HTTP adapter over :mod:`packages.api.backtest`.
These tests assert URL paths, response shapes, validation, admin auth,
and that the BacktestService is called with the right arguments.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app_with_backtest_router(monkeypatch=None):
    """Build a minimal FastAPI app with the backtest router + handlers."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.api.backtest import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/backtest")

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


# ---------------------------------------------------------------------------
# Path registration
# ---------------------------------------------------------------------------


class TestRouterPaths:
    """The router registers the same paths as the FaaS handler."""

    def test_router_routes_registered(self):
        from app.api.backtest import router

        paths = sorted({r.path for r in router.routes})
        assert "/" in paths
        assert "/compare" in paths
        assert "/model-compare" in paths
        assert "/table" in paths
        assert "/seasons" in paths
        assert "/current-season" in paths
        assert "/active-model" in paths
        # BT-1: boosted-tip SHAP routes
        assert "/active-boosted-model" in paths
        assert "/boosted-shap/{game_id}" in paths
        assert "/run" in paths


# ---------------------------------------------------------------------------
# GET /  — deprecated, returns empty results
# ---------------------------------------------------------------------------


class TestBacktestRoot:
    """``GET /api/backtest`` returns the deprecated empty shape."""

    def test_get_root_returns_empty_results(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)
        client = TestClient(app)
        resp = client.get("/api/backtest")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"results": [], "count": 0}


# ---------------------------------------------------------------------------
# GET /compare
# ---------------------------------------------------------------------------


class TestBacktestCompare:
    """``GET /api/backtest/compare`` requires a season."""

    def test_compare_missing_season_returns_422(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/compare")

        assert resp.status_code == 422

    def test_compare_with_season(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_comparison = {
            "best_bet": {
                "total_rounds": 5,
                "total_tips": 30,
                "total_correct": 21,
                "overall_accuracy": 0.7,
                "total_profit": 30.0,
                "avg_profit_per_round": 6.0,
                "best_round_accuracy": 0.83,
                "worst_round_accuracy": 0.6,
            },
            "yolo": {
                "total_rounds": 5,
                "total_tips": 25,
                "total_correct": 12,
                "overall_accuracy": 0.48,
                "total_profit": -10.0,
                "avg_profit_per_round": -2.0,
                "best_round_accuracy": 0.6,
                "worst_round_accuracy": 0.3,
            },
        }

        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc_cls.return_value.compare_heuristics = AsyncMock(
                return_value=mock_comparison
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert "comparison" in body
        assert "best_overall" in body
        # best_overall must point at the heuristic with higher accuracy
        assert body["best_overall"]["heuristic"] == "best_bet"
        assert body["best_overall"]["accuracy"] == 0.7


# ---------------------------------------------------------------------------
# GET /model-compare
# ---------------------------------------------------------------------------


class TestBacktestModelCompare:
    """``GET /api/backtest/model-compare`` requires a season."""

    def test_model_compare_missing_season_returns_422(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/model-compare")

        assert resp.status_code == 422

    def test_model_compare_with_season(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_comparison = [
            {
                "model_name": "elo",
                "season": 2025,
                "total_tips": 30,
                "total_correct": 21,
                "overall_accuracy": 0.7,
                "total_profit": 30.0,
                "avg_margin": 12.0,
            },
            {
                "model_name": "form",
                "season": 2025,
                "total_tips": 30,
                "total_correct": 18,
                "overall_accuracy": 0.6,
                "total_profit": 10.0,
                "avg_margin": 10.0,
            },
        ]

        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc_cls.return_value.compare_models = AsyncMock(
                return_value=mock_comparison
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/model-compare?season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["best_overall"]["model_name"] == "elo"


# ---------------------------------------------------------------------------
# GET /table
# ---------------------------------------------------------------------------


class TestBacktestTable:
    """``GET /api/backtest/table`` requires a season."""

    def test_table_missing_season_returns_422(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/table")

        assert resp.status_code == 422

    def test_table_with_season(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_round_data = [
            {
                "round_id": 1,
                "tips_made": 9,
                "tips_correct": 6,
                "accuracy": 0.667,
                "profit": 10.0,
                "odds_coverage": 0.5,
            }
        ]

        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.get_round_by_round_data = AsyncMock(
                return_value=mock_round_data
            )
            mock_svc.orchestrator.get_available_heuristics.return_value = [
                "best_bet"
            ]

            client = TestClient(app)
            resp = client.get("/api/backtest/table?season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert "heuristics" in body
        assert len(body["heuristics"]) == 1
        heuristic = body["heuristics"][0]
        assert heuristic["heuristic"] == "best_bet"
        assert heuristic["total_profit"] == 10.0


# ---------------------------------------------------------------------------
# GET /seasons
# ---------------------------------------------------------------------------


class TestBacktestSeasons:
    """``GET /api/backtest/seasons`` returns available years + current year."""

    def test_seasons(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[2024, 2025]
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available_years"] == [2024, 2025]
        assert body["current_year"] == datetime.now().year


# ---------------------------------------------------------------------------
# GET /current-season
# ---------------------------------------------------------------------------


class TestBacktestCurrentSeason:
    """``GET /api/backtest/current-season`` returns current season perf."""

    def test_current_season(self):
        from packages.shared.schemas.backtest import (
            CurrentSeasonHeuristicPerformance,
            CurrentSeasonResponse,
        )

        mock_session = AsyncMock(spec=AsyncSession)
        mock_performance = CurrentSeasonResponse(
            season=2025,
            heuristics=[
                CurrentSeasonHeuristicPerformance(
                    heuristic="best_bet",
                    total_profit=30.0,
                    total_accuracy=0.7,
                    rounds_played=5,
                    avg_profit_per_round=6.0,
                    projected_annual_profit=144.0,
                )
            ],
            rounds_completed=5,
            total_rounds=24,
        )
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc_cls.return_value.get_current_season_performance = AsyncMock(
                return_value=mock_performance
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["rounds_completed"] == 5
        assert body["total_rounds"] == 24


# ---------------------------------------------------------------------------
# GET /active-boosted-model  (BT-1)
# ---------------------------------------------------------------------------


class TestBacktestActiveBoostedModel:
    """``GET /api/backtest/active-boosted-model`` returns boosted SHAP meta."""

    def test_active_boosted_model_returns_shap_payload(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_model = {
            "model_name": "boosted_tip",
            "version": 3,
            "trained_at": "2026-09-28T05:00:11+08:00",
            "training_rows": 1234,
            "is_active": True,
            "metrics": {"r2": 0.21, "mae": 24.7, "shap_base_value": 1.8},
            "importances": [
                {
                    "feature_name": "elo_margin_home",
                    "shap_value": 5.2,
                    "model": "elo",
                    "type": "margin",
                },
                {
                    "feature_name": "form_conf",
                    "shap_value": -1.4,
                    "model": "form",
                    "type": "confidence",
                },
            ],
        }

        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch(
            "app.api.backtest.get_active_boosted_model",
            new_callable=AsyncMock,
            return_value=mock_model,
        ) as mock_get:
            client = TestClient(app)
            resp = client.get("/api/backtest/active-boosted-model")

        assert resp.status_code == 200
        body = resp.json()
        assert body == mock_model
        assert body["model_name"] == "boosted_tip"
        assert body["metrics"]["r2"] == pytest.approx(0.21)
        assert body["importances"][0]["feature_name"] == "elo_margin_home"
        assert body["importances"][0]["model"] == "elo"
        assert body["importances"][0]["type"] == "margin"
        mock_get.assert_awaited_once_with(mock_session)

    def test_active_boosted_model_without_active_version_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch(
            "app.api.backtest.get_active_boosted_model",
            new_callable=AsyncMock,
            return_value=None,
        ):
            client = TestClient(app)
            resp = client.get("/api/backtest/active-boosted-model")

        assert resp.status_code == 404
        body = resp.json()
        # Same error shape the repo-wide 404s use (games/events/admin).
        assert body["code"] == "not_found"
        assert "boosted" in body["message"].lower()


# ---------------------------------------------------------------------------
# GET /boosted-shap/{game_id}  (BT-1)
# ---------------------------------------------------------------------------


class TestBacktestBoostedShap:
    """``GET /api/backtest/boosted-shap/{game_id}`` per-game SHAP card."""

    def test_boosted_shap_returns_explanation(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_explanation = {
            "game_id": 42,
            "home_team": "Richmond",
            "away_team": "Carlton",
            "base_value": 1.8,
            "prediction": 9.4,
            "contributions": {"elo_margin_home": 5.2, "elo_conf": 2.4},
            "winner": "home",
            "pick": {"winner": "Richmond", "margin": 9, "confidence": 0.641},
        }

        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch(
            "app.api.backtest.get_game_shap_explanation",
            new_callable=AsyncMock,
            return_value=mock_explanation,
        ) as mock_get:
            client = TestClient(app)
            resp = client.get("/api/backtest/boosted-shap/42")

        assert resp.status_code == 200
        body = resp.json()
        assert body == mock_explanation
        for key in (
            "game_id",
            "home_team",
            "away_team",
            "base_value",
            "prediction",
            "contributions",
            "winner",
            "pick",
        ):
            assert key in body
        assert body["winner"] == "home"
        assert body["pick"]["winner"] == "Richmond"
        # SHAP additivity sanity on the payload itself: base + Σ == prediction.
        assert body["base_value"] + sum(body["contributions"].values()) == pytest.approx(
            body["prediction"]
        )
        mock_get.assert_awaited_once_with(mock_session, 42)

    def test_boosted_shap_without_model_or_predictions_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch(
            "app.api.backtest.get_game_shap_explanation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            client = TestClient(app)
            resp = client.get("/api/backtest/boosted-shap/999999")

        assert resp.status_code == 404
        body = resp.json()
        # Single generic message: the service cannot distinguish "no active
        # model" from "no predictions for the game" (both are None).
        assert body["code"] == "not_found"
        assert body["message"]

    def test_boosted_shap_invalid_game_id_returns_422(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/boosted-shap/not-an-int")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /run  — admin
# ---------------------------------------------------------------------------


class TestBacktestRun:
    """``POST /api/backtest/run`` requires admin key + body."""

    def test_run_missing_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_backtest_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025},
        )
        assert resp.status_code == 401

    def test_run_invalid_api_key_returns_401(self, monkeypatch):
        app = _build_app_with_backtest_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_run_missing_season_returns_422(self, monkeypatch):
        app = _build_app_with_backtest_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/backtest/run",
            json={},
            headers={"X-API-Key": "the-secret-key"},
        )
        assert resp.status_code == 422

    def test_run_success(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = [
            {
                "model_name": "elo",
                "season": 2025,
                "total_tips": 30,
                "total_correct": 21,
                "overall_accuracy": 0.7,
                "total_profit": 30.0,
                "avg_margin": 12.0,
            }
        ]

        app = _build_app_with_backtest_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as mock_svc_cls:
            mock_svc_cls.return_value.run_model_backtest = AsyncMock(
                return_value=mock_stats
            )
            client = TestClient(app)
            resp = client.post(
                "/api/backtest/run",
                json={"season": 2025},
                headers={"X-API-Key": "the-secret-key"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["count"] == 1
        assert body["results"] == mock_stats


# ---------------------------------------------------------------------------
# Lazy heavy-import pin (BT-1 / S3)
# ---------------------------------------------------------------------------


BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

# Same guard list as tests/unit/test_lazy_sklearn.py: importing the
# always-on backtest router (pulled by every API worker) must not pay
# the resident-memory cost of any heavy ML library at module import time.
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")


class TestBacktestRouterLazyImports:
    """Importing ``app.api.backtest`` must not load heavy ML libs (BT-1)."""

    def test_importing_backtest_router_does_not_load_heavy_libs(self):
        """xgboost/shap/sklearn/numpy load only inside request handlers.

        The boosted-tip SHAP routes import the explanation service at
        module level; that is only safe because the service (and its
        whole transitive graph) defers xgboost/shap/numpy imports to
        function bodies.  Runs in an ISOLATED subprocess so the result
        is not polluted by tests that legitimately import heavy libs.
        """
        code = textwrap.dedent(
            f"""
            import sys
            import app.api.backtest
            offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
            assert not offenders, (
                "Importing app.api.backtest loaded heavy ML libs at module "
                "load time: " + repr(offenders)
            )
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
            timeout=120,
        )
        assert result.returncode == 0, (
            "Isolated import check failed — a heavy ML lib was loaded at "
            "import time (or the import itself errored):\n"
            f"--- code ---\n{code}"
            f"--- stdout ---\n{result.stdout}"
            f"--- stderr ---\n{result.stderr}"
        )
