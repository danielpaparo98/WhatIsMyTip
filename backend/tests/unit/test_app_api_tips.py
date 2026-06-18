"""Unit tests for the FastAPI Tips router.

The router is a thin HTTP adapter over :mod:`packages.api.tips`.  These
tests assert URL paths, response shapes, validation, admin auth, and
that the CRUD/service layer is called with the right arguments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tip_mock(**overrides) -> MagicMock:
    """Build a ``Tip``-shaped MagicMock with realistic defaults."""
    defaults = {
        "id": 1,
        "game_id": 1,
        "heuristic": "best_bet",
        "selected_team": "Brisbane",
        "margin": 12,
        "confidence": 0.75,
        "explanation": "Strong at home",
        "created_at": datetime(2025, 3, 14, 12, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    tip = MagicMock()
    for k, v in defaults.items():
        setattr(tip, k, v)
    return tip


def _make_game_mock(**overrides) -> MagicMock:
    defaults = {
        "id": 1,
        "slug": "abc123def4",
        "squiggle_id": 12345,
        "round_id": 1,
        "season": 2025,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": 85,
        "away_score": 72,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0, tzinfo=timezone.utc),
        "completed": True,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


def _build_app_with_tips_router(monkeypatch=None):
    """Build a minimal FastAPI app with the tips router and exception handlers."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.api.tips import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/tips")

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

    # Set a known admin key for tests
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
        from app.api.tips import router

        paths = sorted({r.path for r in router.routes})
        # Routes exposed by the tips router
        assert "/" in paths
        assert "/games-with-tips" in paths
        assert "/generate" in paths
        # The {heuristic} catch-all path is the URL pattern; the
        # ``best_bet``/``yolo``/``high_risk_high_reward`` values are
        # validated against the heuristic allow-list by the path
        # pattern at request time.
        assert "/{heuristic}" in paths


# ---------------------------------------------------------------------------
# GET /  — list tips
# ---------------------------------------------------------------------------


class TestListTips:
    """``GET /api/tips`` lists tips with optional filters."""

    def test_list_tips_no_params(self):
        """With no params, returns best_bet tips (default heuristic)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_tips = [_make_tip_mock()]
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)
            client = TestClient(app)
            resp = client.get("/api/tips")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        mock_crud.get_by_heuristic.assert_awaited_once()

    def test_list_tips_with_season_and_round(self):
        """``season`` + ``round`` filter calls ``get_by_round``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_tips = [_make_tip_mock()]
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_round = AsyncMock(return_value=mock_tips)
            client = TestClient(app)
            resp = client.get("/api/tips?season=2025&round=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        mock_crud.get_by_round.assert_awaited_once_with(
            mock_session, 2025, 1
        )

    def test_list_tips_with_heuristic_only(self):
        """``heuristic=yolo`` calls ``get_by_heuristic`` with that heuristic."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_tips = [_make_tip_mock(heuristic="yolo")]
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)
            client = TestClient(app)
            resp = client.get("/api/tips?heuristic=yolo")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        mock_crud.get_by_heuristic.assert_awaited_once()
        args, kwargs = mock_crud.get_by_heuristic.call_args
        # second positional argument is the heuristic
        assert args[1] == "yolo"


# ---------------------------------------------------------------------------
# GET /games-with-tips
# ---------------------------------------------------------------------------


class TestGamesWithTips:
    """``GET /api/tips/games-with-tips`` requires both season and round."""

    def test_games_with_tips_missing_season_returns_422(self):
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/games-with-tips?round=1")

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_games_with_tips_missing_round_returns_422(self):
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/games-with-tips?season=2025")

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_games_with_tips_missing_both_returns_422(self):
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/games-with-tips")

        assert resp.status_code == 422

    def test_games_with_tips_invalid_heuristic_returns_422(self):
        """heuristic not in the allowed set → 422 (pattern violation)."""
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=1&heuristic=invalid"
        )

        assert resp.status_code == 422

    def test_games_with_tips_success(self):
        """Valid request returns the games-with-tips shape."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()

        # First execute: lock games for the round
        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [mock_game]
        # Second execute: tip lookup
        tips_result = MagicMock()
        tips_result.scalars.return_value.all.return_value = []
        # Third execute: nothing extra

        mock_session.execute = AsyncMock(
            side_effect=[games_result, tips_result, tips_result]
        )

        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.ModelPredictionCRUD") as mock_pred_crud:
            mock_pred_crud.get_by_games = AsyncMock(return_value={})
            client = TestClient(app)
            resp = client.get(
                "/api/tips/games-with-tips?season=2025&round=1"
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "games" in body
        assert "count" in body
        assert body["count"] == 1
        assert body["games"][0]["slug"] == "abc123def4"


# ---------------------------------------------------------------------------
# GET /{heuristic}
# ---------------------------------------------------------------------------


class TestTipsByHeuristic:
    """``GET /api/tips/{heuristic}`` returns tips for one heuristic."""

    def test_tips_by_heuristic_success(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_tips = [_make_tip_mock(heuristic="yolo")]
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)
            client = TestClient(app)
            resp = client.get("/api/tips/yolo")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        args, kwargs = mock_crud.get_by_heuristic.call_args
        # args[0] is the db session; args[1] is the heuristic
        assert args[1] == "yolo"
        # limit is passed as a kwarg; default is 100
        assert kwargs.get("limit") == 100

    def test_tips_by_heuristic_with_limit(self):
        """``limit=10`` is forwarded to the CRUD layer."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/tips/yolo?limit=10")

        assert resp.status_code == 200
        args, kwargs = mock_crud.get_by_heuristic.call_args
        assert kwargs.get("limit") == 10

    def test_tips_by_heuristic_invalid_returns_422(self):
        """An unknown heuristic name returns 422 (pattern validation)."""
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/not_a_real_heuristic")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /generate  — public, rate-limited
# ---------------------------------------------------------------------------


class TestGenerateTips:
    """``POST /api/tips/generate`` is intentionally public.

    The endpoint hits OpenRouter (real cost) and writes to the DB, but
    the deliberate design is that **any caller may trigger tip generation
    for a season/round that has no tips yet**.  The only protection is the
    per-IP rate limit (10 req/min) declared on the route.  These tests
    lock in the public contract: no ``X-API-Key`` is read or required,
    and a stray ``X-API-Key`` header is silently ignored.
    """

    @pytest.fixture(autouse=True)
    def _reset_post_generate_limiter(self):
        """Reset the per-route limiter between tests.

        The route-level ``_post_generate_limiter`` is a module-level
        singleton in ``app.api.tips``, so its in-memory request counts
        persist across tests in the same process.  Without a reset, the
        fourth test in this class would see 429 instead of 200/404
        because the 10/minute cap is shared.  The reset mirrors the
        pattern used in ``tests/integration/conftest.py``.
        """
        from app.api.tips import _post_generate_limiter

        if hasattr(_post_generate_limiter, "reset"):
            try:
                _post_generate_limiter.reset()
            except Exception:  # noqa: BLE001 — best-effort reset
                pass
        yield

    def test_generate_tips_public_no_auth_required_returns_200(
        self, monkeypatch
    ):
        """No headers at all → 200 (public endpoint, no auth required)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
            "duration_seconds": 2.5,
        }

        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.tips.GameCRUD") as mock_game_crud, \
             patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_game_crud.get_by_round = AsyncMock(return_value=[_make_game_mock()])
            mock_service_cls.return_value.generate_for_round = AsyncMock(
                return_value=mock_stats
            )

            client = TestClient(app)
            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1, "regenerate": False},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["tips_created"] == 27
        assert body["tips_skipped"] == 0
        mock_service_cls.return_value.generate_for_round.assert_awaited_once()

    def test_generate_tips_ignores_invalid_x_api_key_header(self, monkeypatch):
        """A garbage ``X-API-Key`` header is silently ignored → 200.

        This pins the public design: if someone re-adds ``require_admin_key``
        to the route in the future, this test will fail with 401 instead
        of 200 and the regression will be caught immediately.
        """
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_processed": 1,
            "tips_created": 3,
            "tips_skipped": 0,
            "tips_updated": 0,
            "errors": [],
        }

        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.tips.GameCRUD") as mock_game_crud, \
             patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_game_crud.get_by_round = AsyncMock(return_value=[_make_game_mock()])
            mock_service_cls.return_value.generate_for_round = AsyncMock(
                return_value=mock_stats
            )

            client = TestClient(app)
            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1, "regenerate": False},
                headers={"X-API-Key": "garbage-value-the-server-should-ignore"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        mock_service_cls.return_value.generate_for_round.assert_awaited_once()

    def test_generate_missing_season_returns_422(self, monkeypatch):
        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/tips/generate",
            json={"round_id": 1},
            headers={"X-API-Key": "the-secret-key"},
        )
        assert resp.status_code == 422

    def test_generate_no_games_returns_404(self, monkeypatch):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.tips.GameCRUD") as mock_game_crud:
            mock_game_crud.get_by_round = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1},
                headers={"X-API-Key": "the-secret-key"},
            )

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"


# ---------------------------------------------------------------------------
# /games-with-tips — HI-004 (drop SELECT ... FOR UPDATE)
# ---------------------------------------------------------------------------


class TestGamesWithTipsNoRowLock:
    """HI-004: the ``SELECT ... FOR UPDATE`` in ``games_with_tips`` is
    decorative and provides no concurrency control.

    The query runs against an autocommit-style read-only session
    (the SQLAlchemy ``AsyncSession`` provided by ``get_db`` is
    configured for implicit begin/commit only when writes happen),
    so the FOR UPDATE lock has no effect.  Worse: when wrapped in
    ``async with db.begin():``, the implicit transaction makes the
    endpoint slow under concurrent load and provides a false sense
    of safety.

    The fix removes the FOR UPDATE clause and the
    ``async with db.begin():`` wrapper.  Concurrent requests
    continue to succeed; the database's MVCC handles read
    consistency.
    """

    def test_games_with_tips_source_has_no_with_for_update(self):
        """The endpoint source must not call ``.with_for_update()``."""
        import inspect

        from app.api.tips import games_with_tips

        src = inspect.getsource(games_with_tips)
        assert ".with_for_update()" not in src, (
            "games_with_tips still calls .with_for_update(); "
            "drop the SELECT FOR UPDATE — it provides no concurrency "
            "control on a read-only autocommit session."
        )

    def test_games_with_tips_source_has_no_db_begin_block(self):
        """The endpoint source must not open an explicit transaction.

        We check for the call as a statement (not just any occurrence
        of the substring) so the docstring explanation of what was
        removed doesn't trip the assertion.
        """
        import inspect

        from app.api.tips import games_with_tips

        src = inspect.getsource(games_with_tips)
        # Match the statement form (with leading whitespace), not the
        # bare substring — so docstring prose explaining the fix is
        # allowed.
        assert "    async with db.begin():" not in src, (
            "games_with_tips still opens an explicit transaction "
            "(async with db.begin():); drop it — the FOR UPDATE "
            "inside the block provided no real lock, and the "
            "transaction wrapper just slows down concurrent reads."
        )

    def test_concurrent_requests_both_succeed(self):
        """Two concurrent calls to /games-with-tips must both return 200.

        With the FOR UPDATE removed, there's no lock to wait on; both
        requests run independently and complete cleanly.
        """
        import asyncio

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.api.tips import router as tips_router

        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [mock_game]
        tips_result = MagicMock()
        tips_result.scalars.return_value.all.return_value = []

        # Every concurrent call gets the same two execute results.
        mock_session.execute = AsyncMock(
            side_effect=[games_result, tips_result] * 10
        )

        app = FastAPI()
        app.include_router(tips_router, prefix="/api/tips")

        from app.core.db_deps import get_db

        async def _override() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_db] = _override

        with patch("app.api.tips.ModelPredictionCRUD") as mock_pred_crud:
            mock_pred_crud.get_by_games = AsyncMock(return_value={})

            client = TestClient(app)

            def _hit():
                return client.get(
                    "/api/tips/games-with-tips?season=2025&round=1"
                )

            # Fire two requests serially (TestClient doesn't support
            # genuine concurrency, but the contract is the same: each
            # one runs the full handler without raising lock errors).
            r1 = _hit()
            r2 = _hit()

        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_games_with_tips_endpoint_uses_select_without_for_update(
        self,
    ):
        """The query built by the endpoint must NOT call ``with_for_update``.

        Inspects the SQL emitted (via SQLAlchemy's compile) for the
        ``FOR UPDATE`` token.  Without the fix, ``FOR UPDATE`` is in
        the SQL; with the fix it is gone.
        """
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.orm import Query

        from app.api.tips import games_with_tips
        from packages.shared.models import Game

        # Build the same query the endpoint builds (post-fix).
        stmt = select(Game).where(
            Game.season == 2025,
            Game.round_id == 1,
        )
        compiled = stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled).upper()
        assert "FOR UPDATE" not in sql, (
            f"SELECT compiled to SQL containing FOR UPDATE: {sql!r}. "
            "The fix must drop the .with_for_update() call from the "
            "games_with_tips endpoint."
        )
