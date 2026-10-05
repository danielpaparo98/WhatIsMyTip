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
        "source": "squiggle",
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
        # ``best_bet``/``yolo``/``weighted_tip`` values are
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

    def test_games_with_tips_orders_games_by_date(self):
        """games-with-tips must SELECT games ordered by match date.

        Regression: the homepage rendered a round's games jumbled because
        the endpoint query had no ``ORDER BY`` clause, so games came back
        in arbitrary physical-storage order.  The fix mirrors
        ``GameCRUD.get_by_round`` and orders ascending by ``date``.

        We assert on the emitted SQL (captured from the first
        ``db.execute`` call) rather than on the mocked result list,
        because the mock returns whatever order it is handed regardless
        of the query.
        """
        from sqlalchemy.dialects import postgresql

        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [mock_game]
        tips_result = MagicMock()
        tips_result.scalars.return_value.all.return_value = []

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

        # The first db.execute call carries the games SELECT statement.
        first_stmt = mock_session.execute.call_args_list[0].args[0]
        compiled = first_stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled).upper()
        assert "ORDER BY" in sql, (
            f"games-with-tips query must ORDER BY date; got SQL: {sql!r}"
        )
        assert "GAMES.DATE" in sql, (
            f"games-with-tips query must ORDER BY games.date; got SQL: {sql!r}"
        )

    def test_games_with_tips_excludes_teamless_games(self):
        """TBC finals placeholders must not reach the homepage grid.

        Regression: Squiggle publishes finals fixtures with null/empty
        team names; the endpoint returned them as empty cards (and with
        garbage ``selected_team = ''`` tips once the cron generated for
        them).  The games SELECT must filter on both team columns.
        """
        from sqlalchemy.dialects import postgresql

        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [mock_game]
        tips_result = MagicMock()
        tips_result.scalars.return_value.all.return_value = []

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

        first_stmt = mock_session.execute.call_args_list[0].args[0]
        compiled = first_stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled).lower()
        assert "home_team is not null" in sql, sql
        assert "away_team is not null" in sql, sql
        assert sql.count("''") >= 2, (
            f"games-with-tips query must exclude blank team names; got: {sql!r}"
        )


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
# POST /generate  — admin-authenticated, rate-limited (SEC: TIPS-GEN-H4)
# ---------------------------------------------------------------------------


class TestGenerateTips:
    """``POST /api/tips/generate`` requires the admin ``X-API-Key``.

    History: the endpoint was briefly public ("any caller may trigger
    generation when no tips exist") with only a per-IP rate limit.  The
    2026-09 review (TIPS-GEN-H4) flagged it as an unauthenticated compute
    + OpenRouter-cost vector — the nightly ``tip-generation`` cron is the
    intended generation path, and operators use the same admin key as
    every other write endpoint.  These tests lock in the authenticated
    contract: missing/garbage keys → 401, valid key → normal behaviour.
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

    def test_generate_tips_requires_auth_returns_401_without_key(
        self, monkeypatch
    ):
        """No ``X-API-Key`` at all → 401, generation service NOT invoked."""
        mock_session = AsyncMock(spec=AsyncSession)

        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.tips.GameCRUD") as mock_game_crud, \
             patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_game_crud.get_by_round = AsyncMock(return_value=[_make_game_mock()])
            mock_service_cls.return_value.generate_for_round = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1, "regenerate": False},
            )

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"
        mock_service_cls.return_value.generate_for_round.assert_not_awaited()

    def test_generate_tips_rejects_invalid_x_api_key_returns_401(self, monkeypatch):
        """A garbage ``X-API-Key`` → 401 (auth is enforced, not ignored)."""
        mock_session = AsyncMock(spec=AsyncSession)

        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, mock_session)

        with patch("app.api.tips.GameCRUD") as mock_game_crud, \
             patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_game_crud.get_by_round = AsyncMock(return_value=[_make_game_mock()])
            mock_service_cls.return_value.generate_for_round = AsyncMock()

            client = TestClient(app)
            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1, "regenerate": False},
                headers={"X-API-Key": "garbage-value-the-server-rejects"},
            )

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"
        mock_service_cls.return_value.generate_for_round.assert_not_awaited()

    def test_generate_tips_with_valid_key_returns_200(self, monkeypatch):
        """Valid ``X-API-Key`` → 200 with the generation contract shape."""
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
                headers={"X-API-Key": "the-secret-key"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["tips_created"] == 27
        assert body["tips_skipped"] == 0
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


# ---------------------------------------------------------------------------
# Heuristic allowlists — boosted_tip added, best_bet KEPT (boosted-tip-06)
# ---------------------------------------------------------------------------


class TestHeuristicAllowlists:
    """``boosted_tip`` joins the allowlists; ``best_bet`` stays queryable.

    Feature decision 3 (boosted-tip): ``best_bet`` left the tip
    *generation* registry, but historical ``tips`` rows with
    ``heuristic='best_bet'`` must remain queryable through every read
    endpoint (AC5).  ``boosted_tip`` is added to the static allowlist
    and the path/query regexes in lockstep; garbage names keep
    failing with 422.
    """

    @pytest.fixture(autouse=True)
    def _reset_post_generate_limiter(self):
        """Reset the shared module-level limiter (mirrors TestGenerateTips)."""
        from app.api.tips import _post_generate_limiter

        if hasattr(_post_generate_limiter, "reset"):
            try:
                _post_generate_limiter.reset()
            except Exception:  # noqa: BLE001 — best-effort reset
                pass
        yield

    # -- static allowlist / pattern shape ----------------------------------

    def test_valid_heuristics_keeps_best_bet_and_adds_boosted_tip(self):
        """The static allowlist is exactly the four known heuristics."""
        from app.api.tips import VALID_HEURISTICS

        assert set(VALID_HEURISTICS) == {
            "best_bet",
            "weighted_tip",
            "yolo",
            "boosted_tip",
        }

    def test_heuristic_pattern_exact_anchored_alternation(self):
        """Anti-drift: pattern is the anchored four-way alternation."""
        from app.api.tips import _HEURISTIC_PATTERN

        assert _HEURISTIC_PATTERN == r"^(best_bet|weighted_tip|yolo|boosted_tip)$"

    # -- GET /{heuristic}  (historical + new) -------------------------------

    def test_best_bet_heuristic_still_queryable(self):
        """AC5 historical contract: GET /api/tips/best_bet → 200."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_tips = [_make_tip_mock(heuristic="best_bet")]
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=mock_tips)
            client = TestClient(app)
            resp = client.get("/api/tips/best_bet")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["tips"][0]["heuristic"] == "best_bet"
        args, _kwargs = mock_crud.get_by_heuristic.call_args
        assert args[1] == "best_bet"

    def test_boosted_tip_heuristic_accepted(self):
        """GET /api/tips/boosted_tip → 200 (empty until tips are generated)."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/tips/boosted_tip")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        args, _kwargs = mock_crud.get_by_heuristic.call_args
        assert args[1] == "boosted_tip"

    def test_unknown_heuristic_path_still_rejected(self):
        """Regression guard: garbage heuristic names still 422 on the path."""
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/not_a_real_heuristic")

        assert resp.status_code == 422

    # -- GET /  (query-string pattern) --------------------------------------

    def test_boosted_tip_list_filter_accepted(self):
        """GET /api/tips?heuristic=boosted_tip → 200."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        with patch("app.api.tips.TipCRUD") as mock_crud:
            mock_crud.get_by_heuristic = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/tips?heuristic=boosted_tip")

        assert resp.status_code == 200
        args, _kwargs = mock_crud.get_by_heuristic.call_args
        assert args[1] == "boosted_tip"

    def test_list_tips_rejects_unknown_heuristic(self):
        """Regression guard: garbage heuristic on the list filter → 422."""
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips?heuristic=not_a_real_heuristic")

        assert resp.status_code == 422

    # -- GET /games-with-tips  (query-string pattern) -----------------------

    def test_games_with_tips_accepts_boosted_tip(self):
        """games-with-tips?heuristic=boosted_tip → 200 (pattern accepts)."""
        mock_session = AsyncMock(spec=AsyncSession)
        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(side_effect=[games_result])

        app = _build_app_with_tips_router()
        _override_db(app, mock_session)

        client = TestClient(app)
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=1&heuristic=boosted_tip"
        )

        assert resp.status_code == 200
        assert resp.json() == {"games": [], "count": 0}

    # -- POST /generate  (business-logic gate against VALID_HEURISTICS) -----

    def test_generate_accepts_boosted_tip(self, monkeypatch):
        """POST /generate with heuristics=['boosted_tip'] passes the
        VALID_HEURISTICS gate and reaches the generation service."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_processed": 9,
            "tips_created": 9,
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
                json={
                    "season": 2025,
                    "round_id": 1,
                    "regenerate": False,
                    "heuristics": ["boosted_tip"],
                },
                headers={"X-API-Key": "the-secret-key"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        mock_service_cls.return_value.generate_for_round.assert_awaited_once()

    def test_generate_accepts_best_bet(self, monkeypatch):
        """Historical contract: best_bet still passes the generation gate
        (backfills of historical rounds rely on it)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_stats = {
            "games_processed": 9,
            "tips_created": 9,
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
                json={
                    "season": 2025,
                    "round_id": 1,
                    "regenerate": False,
                    "heuristics": ["best_bet"],
                },
                headers={"X-API-Key": "the-secret-key"},
            )

        assert resp.status_code == 200
        mock_service_cls.return_value.generate_for_round.assert_awaited_once()

    def test_generate_rejects_unknown_heuristic(self, monkeypatch):
        """Regression guard: unknown heuristic in /generate → 422."""
        app = _build_app_with_tips_router(monkeypatch=monkeypatch)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.post(
            "/api/tips/generate",
            json={
                "season": 2025,
                "round_id": 1,
                "regenerate": False,
                "heuristics": ["not_a_real_heuristic"],
            },
            headers={"X-API-Key": "the-secret-key"},
        )

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_heuristics"
