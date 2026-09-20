"""Unit tests for ``GET /api/games/{slug}/report``.

Same mocking style as ``test_app_api_games.py``: the router is mounted
on a bare FastAPI app, ``get_db`` is overridden with a fake session, and
the CRUD/service boundaries are patched at the router module level.
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
    """Build a ``Game``-shaped MagicMock sitting in GF week (pre-match)."""
    defaults = {
        "id": 101,
        "slug": "gf2026abc",
        "round_id": 27,
        "season": 2026,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": None,
        "away_score": None,
        "venue": "MCG",
        "date": datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
        "completed": False,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


def _report_payload() -> dict:
    """A GrandFinalReport-shaped dict (mirrors the B4 schema exactly)."""
    return {
        "headline": "Lions chase back-to-back against Magpies",
        "executive_summary": "A clash of the season's two most complete sides.",
        "season_story": {
            "home": {
                "team": "Brisbane",
                "narrative": "Dominant at the Gabba all year.",
                "finals_path": ["Qualifying Final: beat Geelong by 22 points"],
            },
            "away": {
                "team": "Collingwood",
                "narrative": "Ground out four tight finals wins.",
                "finals_path": [],
            },
        },
        "keys_to_the_game": ["Stoppage battle", "Inside-50 efficiency"],
        "key_players": {
            "home": [{"name": "L. Neale", "team": "Brisbane", "note": "32 touches a game"}],
            "away": [],
        },
        "injury_watch": {"home": [], "away": []},
        "model_consensus": {
            "summary": "Three of four models lean Brisbane",
            "models_picking_home": 3,
            "models_picking_away": 1,
            "season_accuracy_note": "best_bet tipped 76% for the season",
        },
        "weather_impact": "Clear and 18C — no impact.",
        "x_factor": "Crowd noise at stoppages.",
        "prediction": {"winner": "Brisbane", "margin": 11, "confidence": 0.62},
        "talking_points": ["The midfield arm wrestle decides this."],
    }


def _make_report_row_mock(**overrides) -> MagicMock:
    """Build a ``MatchReport``-shaped MagicMock row."""
    defaults = {
        "id": 7,
        "game_id": 101,
        "report_type": "grand_final_pre_match",
        "report": _report_payload(),
        "created_at": datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


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
# GET /{slug}/report
# ---------------------------------------------------------------------------


class TestGetGameReport:
    """``GET /api/games/{slug}/report`` returns the stored GF report or 404."""

    def test_404_when_game_missing(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_slug = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/games/nonexistent/report")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert body["message"] == "Game not found"

    def test_404_when_not_grand_final(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with (
            patch("app.api.games.GameCRUD") as mock_crud,
            patch("app.api.games.MatchReportService") as mock_service,
            patch("app.api.games.MatchReportCRUD") as mock_report_crud,
        ):
            mock_crud.get_by_slug = AsyncMock(return_value=_make_game_mock())
            mock_service.is_grand_final = AsyncMock(return_value=False)
            mock_report_crud.get_by_game_id = AsyncMock()

            client = TestClient(app)
            resp = client.get("/api/games/abc123def4/report")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert body["message"] == "Match report is only available for the grand final"
        # The CRUD must not be queried for a non-GF game.
        mock_report_crud.get_by_game_id.assert_not_awaited()

    def test_404_when_report_not_generated(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with (
            patch("app.api.games.GameCRUD") as mock_crud,
            patch("app.api.games.MatchReportService") as mock_service,
            patch("app.api.games.MatchReportCRUD") as mock_report_crud,
        ):
            mock_crud.get_by_slug = AsyncMock(return_value=_make_game_mock())
            mock_service.is_grand_final = AsyncMock(return_value=True)
            mock_report_crud.get_by_game_id = AsyncMock(return_value=None)

            client = TestClient(app)
            resp = client.get("/api/games/abc123def4/report")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert body["message"] == "Match report not yet generated"

    def test_200_returns_stored_report_shape(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with (
            patch("app.api.games.GameCRUD") as mock_crud,
            patch("app.api.games.MatchReportService") as mock_service,
            patch("app.api.games.MatchReportCRUD") as mock_report_crud,
        ):
            game = _make_game_mock(id=101)
            mock_crud.get_by_slug = AsyncMock(return_value=game)
            mock_service.is_grand_final = AsyncMock(return_value=True)
            mock_report_crud.get_by_game_id = AsyncMock(
                return_value=_make_report_row_mock()
            )

            client = TestClient(app)
            resp = client.get("/api/games/abc123def4/report")

        assert resp.status_code == 200
        body = resp.json()
        # Response envelope fields (B4 MatchReportResponse).
        assert body["id"] == 7
        assert body["game_id"] == 101
        assert body["report_type"] == "grand_final_pre_match"
        assert body["created_at"].startswith("2026-09-21T03:00:00")
        # Structured report payload matches the GrandFinalReport schema
        # exactly — the frontend mirrors these field names verbatim.
        report = body["report"]
        assert set(report) == {
            "headline",
            "executive_summary",
            "season_story",
            "keys_to_the_game",
            "key_players",
            "injury_watch",
            "model_consensus",
            "weather_impact",
            "x_factor",
            "prediction",
            "talking_points",
        }
        assert report["headline"] == "Lions chase back-to-back against Magpies"
        assert set(report["season_story"]) == {"home", "away"}
        assert set(report["season_story"]["home"]) == {"team", "narrative", "finals_path"}
        assert set(report["key_players"]["home"][0]) == {"name", "team", "note"}
        assert set(report["model_consensus"]) == {
            "summary",
            "models_picking_home",
            "models_picking_away",
            "season_accuracy_note",
        }
        assert set(report["prediction"]) == {"winner", "margin", "confidence"}
        assert report["prediction"]["confidence"] == 0.62

    def test_route_registered_after_detail(self):
        from app.api.games import router

        paths = [r.path for r in router.routes]
        assert "/{slug}/report" in paths
        # Registered after /{slug}/detail (path order matches the docs).
        assert paths.index("/{slug}/report") > paths.index("/{slug}/detail")
