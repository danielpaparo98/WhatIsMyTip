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
        # BT-1: boosted-tip global SHAP route
        assert "/active-boosted-model" in paths
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
# League model set + league branches grade the derived heuristics (FCL-7)
# ---------------------------------------------------------------------------


class TestLeagueModelSetDerivedHeuristics:
    """``_league_model_set`` = the registry's BASE set + the derived trio.

    FCL-7: every league's ``league_tips`` now carries ``best_bet`` /
    ``weighted_tip`` / ``boosted_tip`` rows (FCL-6), so the model set the
    API hands the grading service is the league's resolved BASE set —
    the FULL registry resolution league → sport → default
    (:func:`league_model_set_for_competition`, keyed on the canonical
    competition name) — PLUS the three derived names.  Resolution is
    registry-pure (no DB), so it works before a league's first sync
    exactly like the national-key behaviour it replaces.
    """

    def test_national_key_returns_base_set_plus_derived(self):
        from app.api.backtest import _league_model_set
        from packages.shared.services.league_heuristics import (
            DERIVED_LEAGUE_HEURISTICS,
            RUGBY_LEAGUE_MODELS,
        )

        assert _league_model_set("nrl") == (
            RUGBY_LEAGUE_MODELS + DERIVED_LEAGUE_HEURISTICS
        )

    def test_state_key_returns_parity_base_plus_derived(self):
        from app.api.backtest import _league_model_set
        from packages.shared.services.league_heuristics import (
            DEFAULT_MODEL_SET,
            DERIVED_LEAGUE_HEURISTICS,
        )

        assert _league_model_set("wafl") == (
            DEFAULT_MODEL_SET + DERIVED_LEAGUE_HEURISTICS
        )

    @pytest.mark.parametrize("league", ["nrl", "nrlw", "origin", "wafl", "vfl"])
    def test_derived_tail_is_the_shared_constant_in_canonical_order(self, league):
        from app.api.backtest import _league_model_set
        from packages.shared.services.league_heuristics import (
            DERIVED_LEAGUE_HEURISTICS,
        )

        models = _league_model_set(league)
        assert models is not None
        assert models[-len(DERIVED_LEAGUE_HEURISTICS):] == tuple(
            DERIVED_LEAGUE_HEURISTICS
        )

    def test_unknown_key_resolves_to_none(self):
        # Unreachable via the endpoints (dispatch 404s first) — the
        # helper degrades to ``None`` so the service keeps its default.
        from app.api.backtest import _league_model_set

        assert _league_model_set("not-a-league") is None


class _ScalarResult:
    """``execute()`` result resolving one scalar (the competition lookup)."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _EmptyResult:
    """``execute()`` result with no rows — the unsynced-competition case."""

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return 0

    def all(self):
        return []


def _session_returning(value) -> AsyncMock:
    """AsyncSession mock whose ``execute`` always resolves ``value``."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=value)
    return session


class TestLeagueBranchesGradeDerivedHeuristics:
    """The league branches hand the grading service base + derived (FCL-7).

    ``GET /compare`` and ``GET /current-season`` must grade the three
    derived heuristics from ``league_tips`` at query time, and the
    zero/unsynced payloads must include the derived entries at zero —
    the API passes the full set, the service's own empty-state machinery
    renders it.  Unknown leagues stay a 404 (pinned in
    ``test_backtest_api_league.py`` / ``test_league_backtest_rugby_league.py``).
    """

    def test_compare_state_league_passes_base_plus_derived(self):
        from packages.shared.services.league_heuristics import (
            DEFAULT_MODEL_SET,
            DERIVED_LEAGUE_HEURISTICS,
        )

        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.compare_season = AsyncMock(
                return_value={"season": "2025", "comparison": {}, "best_overall": {}}
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?league=wafl&season=2025")

        assert resp.status_code == 200
        league_cls.return_value.compare_season.assert_awaited_once_with(
            session,
            competition_id=42,
            season_label="2025",
            models=DEFAULT_MODEL_SET + DERIVED_LEAGUE_HEURISTICS,
        )

    def test_compare_national_league_passes_base_plus_derived(self):
        from packages.shared.services.league_heuristics import (
            DERIVED_LEAGUE_HEURISTICS,
            RUGBY_LEAGUE_MODELS,
        )

        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.compare_season = AsyncMock(
                return_value={"season": "2024", "comparison": {}, "best_overall": {}}
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?league=nrl&season=2024")

        assert resp.status_code == 200
        league_cls.return_value.compare_season.assert_awaited_once_with(
            session,
            competition_id=42,
            season_label="2024",
            models=RUGBY_LEAGUE_MODELS + DERIVED_LEAGUE_HEURISTICS,
        )

    def test_current_season_league_passes_base_plus_derived(self):
        from packages.shared.services.league_heuristics import (
            DERIVED_LEAGUE_HEURISTICS,
            RUGBY_LEAGUE_MODELS,
        )

        session = _session_returning(_ScalarResult(7))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_current_season_performance = AsyncMock(
                return_value={"season": "2026", "heuristics": []}
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season?league=origin")

        assert resp.status_code == 200
        league_cls.return_value.get_current_season_performance.assert_awaited_once_with(
            session,
            competition_id=7,
            models=RUGBY_LEAGUE_MODELS + DERIVED_LEAGUE_HEURISTICS,
        )

    def test_unsynced_state_league_zero_payloads_include_derived(self):
        """Registered but never synced: the REAL service runs against an
        empty DB — the zero payload renders base + derived, all zeroed."""
        from packages.shared.services.league_heuristics import (
            DEFAULT_MODEL_SET,
            DERIVED_LEAGUE_HEURISTICS,
        )

        expected = list(DEFAULT_MODEL_SET + DERIVED_LEAGUE_HEURISTICS)
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        current = client.get("/api/backtest/current-season?league=wafl")
        compare = client.get("/api/backtest/compare?league=wafl&season=2025")

        assert current.status_code == 200
        body = current.json()
        assert body["season"] is None
        assert [h["heuristic"] for h in body["heuristics"]] == expected
        assert all(
            h["rounds_played"] == 0 and h["total_profit"] == 0.0
            for h in body["heuristics"]
        )

        assert compare.status_code == 200
        body = compare.json()
        assert set(body["comparison"]) == set(expected)
        assert all(
            stats["total_tips"] == 0 for stats in body["comparison"].values()
        )

    def test_unsynced_national_league_zero_comparison_includes_derived(self):
        from packages.shared.services.league_heuristics import (
            DERIVED_LEAGUE_HEURISTICS,
            RUGBY_LEAGUE_MODELS,
        )

        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=nrl&season=2017")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body["comparison"]) == set(
            RUGBY_LEAGUE_MODELS + DERIVED_LEAGUE_HEURISTICS
        )
        assert all(
            stats["total_tips"] == 0 for stats in body["comparison"].values()
        )
        assert body["best_overall"] == {
            "heuristic": None,
            "accuracy": 0.0,
            "profit": 0.0,
        }


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
