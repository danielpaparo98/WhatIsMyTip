"""Unit tests for the FastAPI Games router.

The router is a thin HTTP adapter over :mod:`packages.api.games` — these
tests assert URL paths, response shapes, validation, error mapping, and
that the CRUD layer is called with the right arguments.  No database or
network access required; CRUD/service functions are mocked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game_mock(**overrides) -> MagicMock:
    """Build a ``Game``-shaped MagicMock with realistic defaults."""
    defaults = {
        "id": 1,
        "slug": "abc123def4",
        "squiggle_id": 12345,
        "round_id": 1,
        "season": 2025,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": None,
        "away_score": None,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0, tzinfo=timezone.utc),
        "completed": False,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


def _make_tip_mock(**overrides) -> MagicMock:
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


def _build_app_with_games_router():
    """Construct a minimal FastAPI app with the games router registered."""
    from fastapi.responses import JSONResponse

    from app.api.games import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/games")

    # Mirror the global exception handler from main.py so 404/4xx errors
    # are converted to the documented JSON shape.
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

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    """Replace the ``get_db`` dependency with a one-shot async generator."""
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
        from app.api.games import router

        paths = sorted({r.path for r in router.routes})
        # Excludes the bare ``/`` (which becomes ``/api/games`` after mount).
        assert "/" in paths
        assert "/{slug}" in paths
        assert "/{slug}/detail" in paths


# ---------------------------------------------------------------------------
# GET / — list games
# ---------------------------------------------------------------------------


class TestListGames:
    """``GET /api/games`` lists games with optional filters."""

    def test_list_games_no_params_returns_upcoming(self):
        """With no params, the FaaS handler defaults to ``get_upcoming``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock(id=1), _make_game_mock(id=2)]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_upcoming = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert len(body["games"]) == 2
        # Confirm get_upcoming was used (not season/round filters)
        mock_crud.get_upcoming.assert_awaited_once()

    def test_list_games_with_season_and_round(self):
        """``season`` + ``round`` filters call ``get_by_round``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock()]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_round = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games?season=2025&round=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # The endpoint now always forwards the bounded ``limit`` to the
        # CRUD layer (default 50) so the SQL cannot scan an entire round.
        mock_crud.get_by_round.assert_awaited_once_with(
            mock_session, 2025, 1, limit=50
        )

    def test_list_games_with_upcoming_true(self):
        """``upcoming=true`` returns upcoming games."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock(completed=False)]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_upcoming = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games?upcoming=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        mock_crud.get_upcoming.assert_awaited_once()

    def test_list_games_with_season_only_passes_default_limit(self):
        """``season`` (no round) calls ``get_by_season`` with default ``limit``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock()]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_season = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games?season=2026")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # Limit is plumbed through (default 50 here) so the SQL is bounded.
        mock_crud.get_by_season.assert_awaited_once_with(
            mock_session, 2026, limit=50
        )

    def test_list_games_with_season_and_limit_passes_limit(self):
        """``?limit=N`` is wired through to ``GameCRUD.get_by_season``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock()]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_season = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games?season=2026&limit=3")

        assert resp.status_code == 200
        mock_crud.get_by_season.assert_awaited_once_with(
            mock_session, 2026, limit=3
        )

    def test_list_games_with_season_and_round_passes_limit(self):
        """``season`` + ``round`` + ``limit`` all flow to ``get_by_round``."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock()]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_round = AsyncMock(return_value=mock_games)
            client = TestClient(app)
            resp = client.get("/api/games?season=2026&round=15&limit=7")

        assert resp.status_code == 200
        mock_crud.get_by_round.assert_awaited_once_with(
            mock_session, 2026, 15, limit=7
        )

    def test_list_games_with_null_team_fields_returns_200(self):
        """Games with NULL home_team/away_team/venue do not cause 500.

        Regression: the 2026 season contains stub game rows from the
        Squiggle future-fixture feed where home_team/away_team/venue are
        NULL in Postgres.  ``GameResponse`` must accept these as nullables.
        """
        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock(
            home_team=None, away_team=None, venue=None
        )
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_upcoming = AsyncMock(return_value=[mock_game])
            client = TestClient(app)
            resp = client.get("/api/games")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["games"][0]["home_team"] is None
        assert body["games"][0]["away_team"] is None
        assert body["games"][0]["venue"] is None

    def test_list_games_with_latest_returns_round_locator(self):
        """``latest=true`` returns the round-locator shape, not a games list."""
        from datetime import datetime as _dt
        from types import SimpleNamespace

        # Use the current year so the ``is_current_year`` flag is True.
        current_year = _dt.now().year

        mock_session = AsyncMock(spec=AsyncSession)

        # First query: future game lookup → row with round_id + season
        # Second query: count for that round → row with season/round_id/game_count
        future_row = SimpleNamespace(round_id=1, season=current_year)
        future_result = MagicMock()
        future_result.first.return_value = future_row
        count_row = SimpleNamespace(
            season=current_year, round_id=1, game_count=9
        )
        count_result = MagicMock()
        count_result.first.return_value = count_row

        mock_session.execute = AsyncMock(side_effect=[future_result, count_result])

        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        client = TestClient(app)
        resp = client.get("/api/games?latest=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "season": current_year,
            "round_id": 1,
            "game_count": 9,
            "is_current_year": True,
            "has_upcoming": True,
        }

    def test_list_games_with_latest_no_data_returns_null_shape(self):
        """``latest=true`` with no games returns a null round-locator shape."""
        mock_session = AsyncMock(spec=AsyncSession)
        # future lookup returns nothing
        future_result = MagicMock()
        future_result.first.return_value = None
        # past lookup returns nothing
        past_result = MagicMock()
        past_result.first.return_value = None

        mock_session.execute = AsyncMock(side_effect=[future_result, past_result])

        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        client = TestClient(app)
        resp = client.get("/api/games?latest=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "season": None,
            "round_id": None,
            "game_count": 0,
            "is_current_year": False,
            "has_upcoming": False,
        }


# ---------------------------------------------------------------------------
# GET /{slug} — single game
# ---------------------------------------------------------------------------


class TestGetGameBySlug:
    """``GET /api/games/{slug}`` returns one game or 404."""

    def test_get_game_by_slug_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock(slug="abc123def4")
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_slug = AsyncMock(return_value=mock_game)
            client = TestClient(app)
            resp = client.get("/api/games/abc123def4")

        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "abc123def4"
        assert body["home_team"] == "Brisbane"

    def test_get_game_by_slug_not_found_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_slug = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/games/nonexistent")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"


# ---------------------------------------------------------------------------
# GET /{slug}/detail
# ---------------------------------------------------------------------------


class TestGetGameDetail:
    """``GET /api/games/{slug}/detail`` returns full detail."""

    def test_get_game_detail_full(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()
        mock_tip = _make_tip_mock()
        mock_pred = MagicMock(
            model_name="elo", winner="Brisbane", confidence=0.7, margin=12
        )
        mock_analysis = MagicMock(
            id=1,
            game_id=1,
            analysis_text="Talking points",
            created_at=datetime(2025, 3, 15, tzinfo=timezone.utc),
        )

        # Mock the weather select() → scalar_one_or_none() returns None
        weather_result = MagicMock()
        weather_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=weather_result)

        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as game_crud, \
             patch("app.api.games.TipCRUD") as tip_crud, \
             patch("app.api.games.ModelPredictionCRUD") as pred_crud, \
             patch("app.api.games.MatchAnalysisCRUD") as analysis_crud:
            game_crud.get_by_slug = AsyncMock(return_value=mock_game)
            tip_crud.get_by_game = AsyncMock(return_value=[mock_tip])
            pred_crud.get_by_game = AsyncMock(return_value=[mock_pred])
            analysis_crud.get_by_game_id = AsyncMock(return_value=mock_analysis)

            client = TestClient(app)
            resp = client.get("/api/games/abc123def4/detail")

        assert resp.status_code == 200
        body = resp.json()
        assert body["game"]["slug"] == "abc123def4"
        assert len(body["tips"]) == 1
        assert len(body["model_predictions"]) == 1
        assert body["model_predictions"][0]["model_name"] == "elo"
        assert body["match_analysis"]["analysis_text"] == "Talking points"
        assert body["weather"] is None

    def test_get_game_detail_without_match_analysis(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_game = _make_game_mock()
        mock_tip = _make_tip_mock()
        mock_pred = MagicMock(
            model_name="elo", winner="Brisbane", confidence=0.7, margin=12
        )

        # Weather row found
        weather_row = MagicMock()
        weather_row.temperature = 22.0
        weather_row.precipitation = 0.0
        weather_row.wind_speed = 5.0
        weather_row.wind_gusts = 8.0
        weather_row.wind_direction = 180
        weather_row.humidity = 60
        weather_row.weather_code = 1
        weather_row.data_type = "historical"
        weather_result = MagicMock()
        weather_result.scalar_one_or_none.return_value = weather_row
        mock_session.execute = AsyncMock(return_value=weather_result)

        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as game_crud, \
             patch("app.api.games.TipCRUD") as tip_crud, \
             patch("app.api.games.ModelPredictionCRUD") as pred_crud, \
             patch("app.api.games.MatchAnalysisCRUD") as analysis_crud:
            game_crud.get_by_slug = AsyncMock(return_value=mock_game)
            tip_crud.get_by_game = AsyncMock(return_value=[mock_tip])
            pred_crud.get_by_game = AsyncMock(return_value=[mock_pred])
            analysis_crud.get_by_game_id = AsyncMock(return_value=None)

            client = TestClient(app)
            resp = client.get("/api/games/abc123def4/detail")

        assert resp.status_code == 200
        body = resp.json()
        assert body["match_analysis"] is None
        # Weather present
        assert body["weather"] is not None
        assert body["weather"]["temperature"] == 22.0

    def test_get_game_detail_not_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as game_crud:
            game_crud.get_by_slug = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/games/nonexistent/detail")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
