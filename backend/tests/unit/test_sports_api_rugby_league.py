"""Unit tests for the rugby-league `league=` param on the public
sport-scoped endpoints (Phase 5.2, subtask 06).

Dispatch contract under test (ADDITIVE — the frozen FaaS surface):

* ``GET /api/events?league=nrl|nrlw|origin`` resolves the league key to
  its ``competitions.id`` by canonical ``competitions.name`` (the
  cross-registry ``get_league`` facade — state first, then national)
  and serves the SAME ``EventListResponse`` shape;
* absent / empty / ``afl`` league → the legacy competition-id path,
  byte-identical (required ``competition`` param, same CRUD call) —
  the registries are never consulted;
* unknown league key                     → 404 repo-standard error
  shape (``BackendServiceError``, never a raw dict), consistent with
  the backtest API's unknown-league behaviour;
* registered but never synced            → the existing
  unknown-competition 404 (no graceful zeros — an event listing has
  no zero shape);
* ``GET /api/sports`` stays pure DB-driven discovery: rugby-league
  rows render through it unchanged once seeded (no sport filtering).

NOTE: since subtask 11's guardrail flip, ``app/api/backtest.py``
resolves through the same cross-registry facade (``league=nrl|nrlw|
origin`` are valid there too, grading the reduced per-sport model set —
see ``test_league_backtest_rugby_league.py``).

No database or network access; the CRUD layer and the competition
lookup are mocked (same conventions as ``test_app_api_events.py`` /
``test_backtest_api_league.py``).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers (same app-builder convention as test_app_api_events.py and
# test_backtest_api_league.py)
# ---------------------------------------------------------------------------


def _build_app_with_events_router():
    """Minimal FastAPI app with the events router + main.py handlers."""
    from app.api.events import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/events")

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

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


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


def _session_returning(value) -> AsyncMock:
    """AsyncSession mock whose ``execute`` always resolves ``value``."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=value)
    return session


def _participant(side="home", name="Broncos", score=28, is_winner=True):
    return {
        "side": side,
        "participant_name": name,
        "score": score,
        "is_winner": is_winner,
    }


def _nrl_event_dict(**overrides) -> dict:
    """API-shaped rugby-league event dict (what EventsCRUD returns)."""
    defaults = {
        "id": 901,
        "slug": "nrl-abc12345",
        "round_id": 1,
        "venue": "Allegiant Stadium",
        "starts_at": datetime(2026, 3, 1, 13, 0),  # naive venue-local
        "status": "completed",
        "completed": True,
        "competition": "National Rugby League",
        "season": "2026",
        "participants": [
            _participant(),
            _participant("away", "Cowboys", 18, False),
        ],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# GET /api/sports — discovery stays pure DB-driven (frozen contract)
# ---------------------------------------------------------------------------


class TestSportsDiscoveryRugbyLeague:
    """The discovery surface renders rugby-league rows data-driven —
    no sport filtering exists to update (the additive contract)."""

    def test_rugby_league_sport_renders_through_discovery(self):
        from app.api.sports import router as sports_router

        app = _build_app_with_events_router()
        app.include_router(sports_router, prefix="/api/sports")
        _override_db(app, AsyncMock(spec=AsyncSession))

        payload = [
            {
                "id": "rugby-league",
                "display_name": "Rugby League",
                "competitions": [
                    {
                        "id": 42,
                        "sport_id": "rugby-league",
                        "name": "National Rugby League",
                        "tier": "national",
                        "format": "rounds",
                        "timezone": "Australia/Brisbane",
                        "seasons": [
                            {
                                "id": 77,
                                "label": "2026",
                                "start_date": None,
                                "end_date": None,
                                "is_current": True,
                            }
                        ],
                    }
                ],
            }
        ]
        with patch(
            "packages.shared.crud.sports.SportsCRUD.list_sports_with_competitions",
            AsyncMock(return_value=payload),
        ):
            client = TestClient(app)
            resp = client.get("/api/sports")

        assert resp.status_code == 200
        body = resp.json()
        assert body["sports"][0]["id"] == "rugby-league"
        competition = body["sports"][0]["competitions"][0]
        assert competition["name"] == "National Rugby League"
        assert competition["tier"] == "national"
        assert competition["seasons"][0]["label"] == "2026"


# ---------------------------------------------------------------------------
# GET /api/events?league=nrl|nrlw|origin — the new additive param
# ---------------------------------------------------------------------------


class TestLeagueParamAccepted:
    """Each national key resolves to its competition id and serves the
    SAME EventListResponse shape."""

    def test_events_league_nrl_resolves_competition(self):
        session = _session_returning(_ScalarResult(42))
        events = [_nrl_event_dict()]
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=events)
            client = TestClient(app)
            resp = client.get("/api/events?league=nrl&season=2026")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        event = body["events"][0]
        assert event["slug"] == "nrl-abc12345"
        assert event["competition"] == "National Rugby League"
        assert event["participants"][0]["participant_name"] == "Broncos"
        # The registry resolved the id; no explicit competition param needed.
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            session, 42, "2026", None, limit=100
        )

    def test_events_league_nrlw_resolves_competition(self):
        session = _session_returning(_ScalarResult(43))
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?league=nrlw&season=2026")

        assert resp.status_code == 200
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            session, 43, "2026", None, limit=100
        )

    def test_events_league_origin_resolves_competition(self):
        session = _session_returning(_ScalarResult(44))
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?league=origin&season=2026")

        assert resp.status_code == 200
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            session, 44, "2026", None, limit=100
        )

    def test_league_with_round_and_limit_forwarded_to_crud(self):
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get(
                "/api/events?league=nrl&season=2026&round=2&limit=7"
            )

        assert resp.status_code == 200
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            session, 42, "2026", 2, limit=7
        )

    def test_league_season_with_no_events_returns_empty_list(self):
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?league=nrl&season=2026")

        assert resp.status_code == 200
        assert resp.json() == {"events": [], "count": 0}

    def test_registered_but_unsynced_league_returns_404(self):
        # No competitions row yet → the existing unknown-competition
        # 404 (an event listing has no graceful zero shape).
        session = _session_returning(_EmptyResult())
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/events?league=nrl&season=2026")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert body["message"] == "Competition or season not found"

    def test_unknown_league_returns_404_repo_shape(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            client = TestClient(app)
            resp = client.get("/api/events?league=nonsense&season=2026")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "nonsense" in body["message"].lower()
        # Failed at the registry — nothing was queried.
        mock_crud.get_by_competition_season.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_league_keys_are_case_sensitive(self):
        # Same contract as the backtest API's league dispatch.
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            client = TestClient(app)
            resp = client.get("/api/events?league=NRL&season=2026")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        mock_crud.get_by_competition_season.assert_not_called()

    def test_multiple_competition_matches_return_404(self):
        # Uniqueness on competitions is (sport_id, name); a clashing
        # name must surface as the repo-standard 404, never a 500
        # (mirrors the backtest resolver's Review #5 guard).
        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock(side_effect=MultipleResultsFound())
        app = _build_app_with_events_router()
        _override_db(app, session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            client = TestClient(app)
            resp = client.get("/api/events?league=nrl&season=2026")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        mock_crud.get_by_competition_season.assert_not_called()


# ---------------------------------------------------------------------------
# Frozen contract — absent / empty / 'afl' league is byte-identical AFL
# ---------------------------------------------------------------------------


class TestLegacyPathUnchanged:
    """The legacy competition-id path is untouched by the league param."""

    def test_no_league_rides_legacy_competition_path(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(
                return_value=[_nrl_event_dict()]
            )
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=2026")

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=100
        )
        # No registry resolution — the DB is only touched by the CRUD.
        mock_session.execute.assert_not_called()

    def test_league_afl_rides_legacy_competition_path(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get(
                "/api/events?competition=1&season=2026&league=afl"
            )

        assert resp.status_code == 200
        assert resp.json() == {"events": [], "count": 0}
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=100
        )
        mock_session.execute.assert_not_called()

    def test_empty_league_rides_legacy_competition_path(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=2026&league=")

        assert resp.status_code == 200
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=100
        )

    def test_no_league_without_competition_returns_422(self):
        # ``competition`` stays effectively required on the legacy path.
        app = _build_app_with_events_router()
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.events.EventsCRUD") as mock_crud:
            client = TestClient(app)
            resp = client.get("/api/events?season=2026")

        assert resp.status_code == 422
        mock_crud.get_by_competition_season.assert_not_called()

    def test_league_afl_without_competition_returns_422(self):
        app = _build_app_with_events_router()
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.events.EventsCRUD") as mock_crud:
            client = TestClient(app)
            resp = client.get("/api/events?season=2026&league=afl")

        assert resp.status_code == 422
        mock_crud.get_by_competition_season.assert_not_called()

    def test_non_integer_competition_returns_422(self):
        app = _build_app_with_events_router()
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.events.EventsCRUD"):
            client = TestClient(app)
            resp = client.get("/api/events?competition=abc&season=2026")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Registry-contract sanity: the resolver reads the cross-registry facade
# ---------------------------------------------------------------------------


class TestRegistryContract:
    """The canonical names the competition lookup matches."""

    def test_national_keys_resolve_canonical_names(self):
        from packages.shared.ingestion.national_leagues import get_league

        expected = {
            "nrl": "National Rugby League",
            "nrlw": "NRL Women's Premiership",
            "origin": "State of Origin",
        }
        for key, name in expected.items():
            assert get_league(key).name == name

    def test_facade_covers_state_leagues_too(self):
        from packages.shared.ingestion.national_leagues import get_league

        assert get_league("wafl").name == "West Australian Football League"
