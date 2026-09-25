"""Unit tests for the ADR 0001 deprecation-window increment on
``/api/games``.

Two behaviours are covered:

1. ``GET /api/games?competition=...`` serves events-table competitions
   through the legacy ``GameListResponse`` shape via the
   :func:`event_to_game_response` adapter (participants flattened to
   home/away, ``squiggle_id`` null, ``source`` populated).
2. ALL ``/api/games`` responses carry the deprecation headers
   (``Deprecation``, ``Sunset``, ``Link``) — the legacy path's behaviour
   is otherwise unchanged.

No database or network access required; the CRUD layer is mocked.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SUNSET_PATTERN = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} "
    r"00:00:00 GMT$"
)


def _participant(side="home", name="Peel Thunder", score=91, is_winner=True):
    return {
        "side": side,
        "participant_name": name,
        "score": score,
        "is_winner": is_winner,
    }


def _make_event_dict(**overrides) -> dict:
    """Build an API-shaped event dict (what EventsCRUD returns)."""
    defaults = {
        "id": 501,
        "slug": "wafl-abc12345",
        "round_id": 1,
        "venue": "Lane Group Stadium",
        "starts_at": datetime(2026, 4, 3, 13, 10),  # naive venue-local
        "status": "completed",
        "completed": True,
        "competition": "West Australian Football League",
        "season": "2026",
        "participants": [
            _participant(),
            _participant("away", "East Fremantle", 78, False),
        ],
    }
    defaults.update(overrides)
    return defaults


def _make_game_mock(**overrides) -> MagicMock:
    """Build a ``Game``-shaped MagicMock with realistic defaults."""
    defaults = {
        "id": 1,
        "slug": "abc123def4",
        "squiggle_id": 12345,
        "source": "squiggle",
        "round_id": 1,
        "season": 2025,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": None,
        "away_score": None,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0),
        "completed": False,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


def _build_app_with_games_router():
    """Construct a minimal FastAPI app with the games router registered."""
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
# Deprecation headers (ALL /api/games responses)
# ---------------------------------------------------------------------------


class TestDeprecationHeaders:
    """Every /api/games response carries the ADR 0001 deprecation headers."""

    def test_list_games_legacy_response_has_headers(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_upcoming = AsyncMock(return_value=[_make_game_mock()])
            client = TestClient(app)
            resp = client.get("/api/games")

        assert resp.status_code == 200
        assert resp.headers["deprecation"] == "true"
        assert SUNSET_PATTERN.match(resp.headers["sunset"])
        assert (
            resp.headers["link"]
            == '<https://whatismytip.com/api/events>; rel="alternate"'
        )

    def test_list_games_events_response_has_headers(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value="2026")
            mock_events.get_by_competition_season = AsyncMock(
                return_value=[_make_event_dict()]
            )
            client = TestClient(app)
            resp = client.get("/api/games?competition=1")

        assert resp.status_code == 200
        assert resp.headers["deprecation"] == "true"
        assert SUNSET_PATTERN.match(resp.headers["sunset"])
        assert (
            resp.headers["link"]
            == '<https://whatismytip.com/api/events>; rel="alternate"'
        )

    def test_get_game_by_slug_has_headers(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_by_slug = AsyncMock(return_value=_make_game_mock())
            client = TestClient(app)
            resp = client.get("/api/games/abc123def4")

        assert resp.status_code == 200
        assert resp.headers["deprecation"] == "true"
        assert SUNSET_PATTERN.match(resp.headers["sunset"])

    def test_sunset_is_exactly_six_months_after_deprecation_constant(self):
        """The Sunset header is computed from the deprecation constant
        (+6 months) at import time, in HTTP-date format."""
        from datetime import date

        from app.api.games import (
            GAMES_API_DEPRECATION_DATE,
            GAMES_API_SUNSET_HEADER,
        )

        total = GAMES_API_DEPRECATION_DATE.month - 1 + 6
        expected_year = GAMES_API_DEPRECATION_DATE.year + total // 12
        expected_month = total % 12 + 1
        assert SUNSET_PATTERN.match(GAMES_API_SUNSET_HEADER)
        assert str(expected_year) in GAMES_API_SUNSET_HEADER
        assert isinstance(GAMES_API_DEPRECATION_DATE, date)


# ---------------------------------------------------------------------------
# Legacy path unchanged (no competition param)
# ---------------------------------------------------------------------------


class TestLegacyPathUnchanged:
    """Without ``competition``, the route hits the legacy CRUD path only."""

    def test_no_competition_uses_legacy_crud_and_not_events(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_games = [_make_game_mock(id=1), _make_game_mock(id=2)]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud, patch(
            "app.api.games.EventsCRUD"
        ) as mock_events:
            mock_crud.get_upcoming = AsyncMock(return_value=mock_games)
            mock_events.get_latest_season_label = AsyncMock()
            mock_events.get_by_competition_season = AsyncMock()
            client = TestClient(app)
            resp = client.get("/api/games")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        mock_crud.get_upcoming.assert_awaited_once()
        mock_events.get_latest_season_label.assert_not_awaited()
        mock_events.get_by_competition_season.assert_not_awaited()

    def test_season_round_filters_still_hit_legacy_crud(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud, patch(
            "app.api.games.EventsCRUD"
        ) as mock_events:
            mock_crud.get_by_round = AsyncMock(return_value=[_make_game_mock()])
            mock_events.get_by_competition_season = AsyncMock()
            client = TestClient(app)
            resp = client.get("/api/games?season=2025&round=1")

        assert resp.status_code == 200
        mock_crud.get_by_round.assert_awaited_once_with(
            mock_session, 2025, 1, limit=50
        )
        mock_events.get_by_competition_season.assert_not_awaited()

    def test_legacy_body_shape_unchanged(self):
        """The legacy list body keeps squiggle_id + source=squiggle."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.GameCRUD") as mock_crud:
            mock_crud.get_upcoming = AsyncMock(return_value=[_make_game_mock()])
            client = TestClient(app)
            resp = client.get("/api/games")

        body = resp.json()
        game = body["games"][0]
        assert game["squiggle_id"] == 12345
        assert game["source"] == "squiggle"


# ---------------------------------------------------------------------------
# GET /api/games?competition=... — events-table competitions
# ---------------------------------------------------------------------------


class TestCompetitionServesEvents:
    """``competition`` serves events through the legacy response shape."""

    def test_competition_and_season_label_served_from_events(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events, patch(
            "app.api.games.GameCRUD"
        ) as mock_crud:
            mock_events.get_by_competition_season = AsyncMock(
                return_value=[_make_event_dict()]
            )
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&season_label=2026")

        assert resp.status_code == 200
        mock_events.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=500
        )
        mock_crud.get_upcoming.assert_not_called()

        body = resp.json()
        assert body["count"] == 1
        game = body["games"][0]
        # Participants flattened to the legacy home/away columns.
        assert game["home_team"] == "Peel Thunder"
        assert game["away_team"] == "East Fremantle"
        assert game["home_score"] == 91
        assert game["away_score"] == 78
        # Provider-specific id absent for events rows; source populated.
        assert game["squiggle_id"] is None
        assert game["source"] == "events"
        # Legacy-shape pass-through fields.
        assert game["id"] == 501
        assert game["slug"] == "wafl-abc12345"
        assert game["round_id"] == 1
        assert game["season"] == 2026  # int(label)
        assert game["venue"] == "Lane Group Stadium"
        assert game["date"] == "2026-04-03T13:10:00"
        assert game["completed"] is True

    def test_season_label_defaults_to_latest_by_label(self):
        """No ``season_label`` → latest season label of the competition."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value="2026")
            mock_events.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/games?competition=1")

        assert resp.status_code == 200
        mock_events.get_latest_season_label.assert_awaited_once_with(
            mock_session, 1
        )
        mock_events.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=500
        )
        assert resp.json() == {"games": [], "count": 0}

    def test_unknown_competition_returns_404(self):
        """Latest-label resolution returning None → competition unknown."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/games?competition=999")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_unknown_season_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_by_competition_season = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&season_label=1999")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_known_season_without_events_returns_empty_list(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&season_label=2026")

        assert resp.status_code == 200
        assert resp.json() == {"games": [], "count": 0}


# ---------------------------------------------------------------------------
# Round / latest / upcoming filters over events
# ---------------------------------------------------------------------------


class TestEventFilters:
    """``round`` maps to round_id; ``latest``/``upcoming`` over events."""

    def test_round_maps_to_round_id(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_by_competition_season = AsyncMock(
                return_value=[_make_event_dict(round_id=5)]
            )
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&season_label=2026&round=5")

        assert resp.status_code == 200
        mock_events.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", 5, limit=500
        )
        assert resp.json()["games"][0]["round_id"] == 5

    def test_latest_returns_max_round_with_events(self):
        mock_session = AsyncMock(spec=AsyncSession)
        events = [
            _make_event_dict(id=501, slug="e-501", round_id=1),
            _make_event_dict(id=503, slug="e-503", round_id=3),
            _make_event_dict(id=504, slug="e-504", round_id=3),
        ]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value="2026")
            mock_events.get_by_competition_season = AsyncMock(return_value=events)
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&latest=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert {g["round_id"] for g in body["games"]} == {3}
        assert {g["id"] for g in body["games"]} == {503, 504}

    def test_upcoming_filters_completed_and_orders_by_starts_at(self):
        mock_session = AsyncMock(spec=AsyncSession)
        events = [
            _make_event_dict(
                id=501,
                slug="e-501",
                completed=True,
                status="completed",
                starts_at=datetime(2026, 4, 3, 13, 10),
            ),
            _make_event_dict(
                id=502,
                slug="e-502",
                completed=False,
                status="scheduled",
                starts_at=datetime(2026, 4, 10, 13, 10),
            ),
            _make_event_dict(
                id=503,
                slug="e-503",
                completed=False,
                status="scheduled",
                starts_at=datetime(2026, 4, 9, 13, 10),
            ),
        ]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value="2026")
            mock_events.get_by_competition_season = AsyncMock(return_value=events)
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&upcoming=true")

        assert resp.status_code == 200
        body = resp.json()
        assert [g["id"] for g in body["games"]] == [503, 502]
        assert all(g["completed"] is False for g in body["games"])


# ---------------------------------------------------------------------------
# Pagination passthrough (limit / offset)
# ---------------------------------------------------------------------------


class TestEventPagination:
    """``limit``/``offset`` page the adapter output."""

    def test_limit_and_offset_slice_events(self):
        mock_session = AsyncMock(spec=AsyncSession)
        events = [
            _make_event_dict(id=501, slug="e-501"),
            _make_event_dict(id=502, slug="e-502"),
            _make_event_dict(id=503, slug="e-503"),
        ]
        app = _build_app_with_games_router()
        _override_db(app, mock_session)

        with patch("app.api.games.EventsCRUD") as mock_events:
            mock_events.get_latest_season_label = AsyncMock(return_value="2026")
            mock_events.get_by_competition_season = AsyncMock(return_value=events)
            client = TestClient(app)
            resp = client.get("/api/games?competition=1&limit=1&offset=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["games"][0]["id"] == 502


# ---------------------------------------------------------------------------
# event_to_game_response adapter (unit)
# ---------------------------------------------------------------------------


class TestEventToGameResponse:
    """The dict-level event → GameResponse mapping."""

    def test_full_mapping(self):
        from packages.shared.crud.events_adapter import event_to_game_response

        evt = _make_event_dict()
        result = event_to_game_response(evt)

        assert result == {
            "id": 501,
            "slug": "wafl-abc12345",
            "squiggle_id": None,
            "source": "events",
            "round_id": 1,
            "season": 2026,
            "home_team": "Peel Thunder",
            "away_team": "East Fremantle",
            "home_score": 91,
            "away_score": 78,
            "venue": "Lane Group Stadium",
            "date": datetime(2026, 4, 3, 13, 10),
            "completed": True,
        }

    def test_maps_into_gameschema(self):
        """The adapter output validates as a GameResponse."""
        from packages.shared.crud.events_adapter import event_to_game_response
        from packages.shared.schemas import GameResponse

        result = event_to_game_response(_make_event_dict())
        game = GameResponse.model_validate(result)
        assert game.squiggle_id is None
        assert game.source == "events"
        assert game.home_team == "Peel Thunder"

    def test_missing_away_side_yields_none_fields(self):
        from packages.shared.crud.events_adapter import event_to_game_response

        evt = _make_event_dict(participants=[_participant()])
        result = event_to_game_response(evt)

        assert result is not None
        assert result["home_team"] == "Peel Thunder"
        assert result["away_team"] is None
        assert result["away_score"] is None

    def test_na_sides_only_rejected(self):
        """A race/field event with only ``n/a`` sides maps to None —
        there is no legacy home/away fixture to serve."""
        from packages.shared.crud.events_adapter import event_to_game_response

        evt = _make_event_dict(
            participants=[
                _participant("n/a", "Lane Group", None, None),
                _participant("n/a", "Scratchy Runner", None, None),
            ]
        )
        assert event_to_game_response(evt) is None

    def test_round_id_none_maps_to_zero(self):
        """Tournament/race events have no round; legacy shape needs an int
        (mirrors the legacy create path's ``round_id or 0``)."""
        from packages.shared.crud.events_adapter import event_to_game_response

        evt = _make_event_dict(round_id=None)
        assert event_to_game_response(evt)["round_id"] == 0

    def test_non_numeric_season_label_maps_to_zero(self):
        from packages.shared.crud.events_adapter import event_to_game_response

        evt = _make_event_dict(season="2026-27")
        assert event_to_game_response(evt)["season"] == 0


# ---------------------------------------------------------------------------
# EventsCRUD.get_latest_season_label (mocked db.execute)
# ---------------------------------------------------------------------------


class TestEventsCRUDLatestSeasonLabel:
    async def test_returns_label_value(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        result = MagicMock()
        result.first.return_value = ("2026",)
        db.execute = AsyncMock(return_value=result)

        assert await EventsCRUD.get_latest_season_label(db, 1) == "2026"

    async def test_no_seasons_returns_none(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        result = MagicMock()
        result.first.return_value = None
        db.execute = AsyncMock(return_value=result)

        assert await EventsCRUD.get_latest_season_label(db, 999) is None
