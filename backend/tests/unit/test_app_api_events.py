"""Unit tests for the ``/api/events`` read-side endpoints (ADR 0001).

The first read-side cutover increment: event-scoped public routes
serving from the 0010 events tables, so multi-league data becomes
reachable by the site.  These tests assert URL paths, response shapes,
competition/season resolution (404s), participant nesting, and that the
CRUD layer is called with the right arguments.  No database or network
access required; the CRUD layer is mocked.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _participant_dict(side="home", name="Peel Thunder", score=91, is_winner=True):
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
            _participant_dict(),
            _participant_dict("away", "East Fremantle", 78, False),
        ],
    }
    defaults.update(overrides)
    return defaults


def _build_app_with_events_router(double_mount: bool = False):
    """Construct a minimal FastAPI app with the events router registered.

    With ``double_mount=True`` mirrors main.py's ingress-trim pattern:
    the router is mounted at both ``/api/events`` (documented) and
    ``/events`` (trimmed path forwarded by production ingress).
    """
    from app.api.events import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/events")
    if double_mount:
        app.include_router(router, prefix="/events", include_in_schema=False)

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
    """The router registers the event-scoped read paths."""

    def test_router_routes_registered(self):
        from app.api.events import router

        paths = sorted({r.path for r in router.routes})
        assert "/" in paths
        assert "/{slug}" in paths


# ---------------------------------------------------------------------------
# GET / — list events for a competition season
# ---------------------------------------------------------------------------


class TestListEvents:
    """``GET /api/events`` lists events joined with their participants."""

    def test_list_returns_events_with_nested_participants(self):
        mock_session = AsyncMock(spec=AsyncSession)
        events = [_make_event_dict(), _make_event_dict(id=502, slug="wafl-def67890")]
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=events)
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=2026")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        event = body["events"][0]
        assert event["slug"] == "wafl-abc12345"
        assert event["competition"] == "West Australian Football League"
        assert event["season"] == "2026"
        # Participant nesting: side, name, score, is_winner
        assert event["participants"][0] == {
            "side": "home",
            "participant_name": "Peel Thunder",
            "score": 91,
            "is_winner": True,
        }
        assert event["participants"][1]["side"] == "away"
        # Default limit (100) is plumbed through; no round filter.
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", None, limit=100
        )

    def test_unknown_competition_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/events?competition=999&season=2026")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"

    def test_unknown_season_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=1999")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"

    def test_round_and_limit_forwarded_to_crud(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get(
                "/api/events?competition=1&season=2026&round=5&limit=7"
            )

        assert resp.status_code == 200
        mock_crud.get_by_competition_season.assert_awaited_once_with(
            mock_session, 1, "2026", 5, limit=7
        )

    def test_season_with_no_events_returns_empty_list(self):
        """A known competition/season with zero events is a 200 empty
        list — distinct from the 404 unknown-competition/season case."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=2026")

        assert resp.status_code == 200
        assert resp.json() == {"events": [], "count": 0}

    def test_ingress_alias_resolves_without_redirect(self):
        """The ingress-trim alias: ``GET /api/events`` (no trailing slash)
        must resolve directly — production ingress trims ``/api`` and
        would otherwise 307 to ``/events/``.  Same double-mount pattern
        as games in main.py."""
        app = _build_app_with_events_router(double_mount=True)
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/api/events?competition=1&season=2026", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.json() == {"events": [], "count": 0}

    def test_trimmed_path_also_serves(self):
        """The ``/events`` trimmed mount serves the same handler."""
        app = _build_app_with_events_router(double_mount=True)
        _override_db(app, AsyncMock(spec=AsyncSession))

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_competition_season = AsyncMock(return_value=[])
            client = TestClient(app)
            resp = client.get("/events?competition=1&season=2026", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.json() == {"events": [], "count": 0}


# ---------------------------------------------------------------------------
# GET /{slug} — single event
# ---------------------------------------------------------------------------


class TestGetEventBySlug:
    """``GET /api/events/{slug}`` returns one event with participants."""

    def test_get_event_by_slug_found(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_slug_with_participants = AsyncMock(
                return_value=_make_event_dict()
            )
            client = TestClient(app)
            resp = client.get("/api/events/wafl-abc12345")

        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "wafl-abc12345"
        assert body["venue"] == "Lane Group Stadium"
        assert body["status"] == "completed"
        assert body["completed"] is True
        assert len(body["participants"]) == 2
        assert body["participants"][0]["participant_name"] == "Peel Thunder"
        assert body["participants"][1]["is_winner"] is False

    def test_get_event_by_slug_not_found_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_events_router()
        _override_db(app, mock_session)

        with patch("app.api.events.EventsCRUD") as mock_crud:
            mock_crud.get_by_slug_with_participants = AsyncMock(return_value=None)
            client = TestClient(app)
            resp = client.get("/api/events/does-not-exist")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"


# ---------------------------------------------------------------------------
# EventsCRUD (mocked db.execute — mirrors test_app_api_sports.py style)
# ---------------------------------------------------------------------------


def _season_row():
    return SimpleNamespace(
        id=9,
        competition_id=1,
        label="2026",
        start_date=None,
        end_date=None,
        is_current=True,
    )


def _competition_row():
    return SimpleNamespace(
        id=1,
        sport_id="afl",
        name="West Australian Football League",
        tier="state",
        format="rounds",
        timezone="Australia/Perth",
    )


def _event_row(event_id=501):
    return SimpleNamespace(
        id=event_id,
        season_id=9,
        event_type="match",
        round_id=1,
        venue="Lane Group Stadium",
        starts_at=datetime(2026, 4, 3, 13, 10),
        status="completed",
        completed=True,
        slug="wafl-abc12345",
        sync_version=1,
        last_synced_at=None,
    )


def _event_participant_row(event_id=501, ep_id=1, side="home", score=91, is_winner=True):
    return SimpleNamespace(
        id=ep_id,
        event_id=event_id,
        participant_id=71 if side == "home" else 72,
        side=side,
        score=score,
        is_winner=is_winner,
    )


class TestEventsCRUDGetByCompetitionSeason:
    @pytest.mark.asyncio
    async def test_returns_events_with_grouped_participants(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)

        season_row = _season_row()
        comp_row = _competition_row()
        season_result = MagicMock()
        season_result.first.return_value = (season_row, comp_row)

        events_result = MagicMock()
        events_result.scalars.return_value.all.return_value = [
            _event_row(501),
            _event_row(502),
        ]

        participant_rows = [
            (_event_participant_row(501, 1, "home", 91, True), "Peel Thunder"),
            (_event_participant_row(501, 2, "away", 78, False), "East Fremantle"),
            (_event_participant_row(502, 3, "home", 55, False), "South Fremantle"),
            (_event_participant_row(502, 4, "away", 60, True), "Claremont"),
        ]
        participants_result = MagicMock()
        participants_result.all.return_value = participant_rows

        # Query order: season+competition lookup → events → participants.
        db.execute = AsyncMock(
            side_effect=[season_result, events_result, participants_result]
        )

        result = await EventsCRUD.get_by_competition_season(db, 1, "2026")

        assert result is not None
        assert len(result) == 2
        first = result[0]
        assert first["competition"] == "West Australian Football League"
        assert first["season"] == "2026"
        assert [p["participant_name"] for p in first["participants"]] == [
            "Peel Thunder",
            "East Fremantle",
        ]
        assert first["participants"][0]["is_winner"] is True
        # No cross-event bleed (no N+1, single participants query).
        assert [p["participant_name"] for p in result[1]["participants"]] == [
            "South Fremantle",
            "Claremont",
        ]

    @pytest.mark.asyncio
    async def test_unknown_competition_or_season_returns_none(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        season_result = MagicMock()
        season_result.first.return_value = None
        db.execute = AsyncMock(return_value=season_result)

        assert await EventsCRUD.get_by_competition_season(db, 999, "1999") is None

    @pytest.mark.asyncio
    async def test_known_season_without_events_returns_empty_list(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        season_result = MagicMock()
        season_result.first.return_value = (_season_row(), _competition_row())
        events_result = MagicMock()
        events_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[season_result, events_result])

        assert await EventsCRUD.get_by_competition_season(db, 1, "2026") == []

    @pytest.mark.asyncio
    async def test_three_queries_only_no_n_plus_one(self):
        """Two events must still cost exactly three queries:
        season+competition, events, participants (batched via IN)."""
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        season_result = MagicMock()
        season_result.first.return_value = (_season_row(), _competition_row())
        events_result = MagicMock()
        events_result.scalars.return_value.all.return_value = [
            _event_row(501),
            _event_row(502),
        ]
        participants_result = MagicMock()
        participants_result.all.return_value = [
            (_event_participant_row(501, 1, "home", 91, True), "Peel Thunder"),
        ]
        db.execute = AsyncMock(
            side_effect=[season_result, events_result, participants_result]
        )

        await EventsCRUD.get_by_competition_season(db, 1, "2026")

        assert db.execute.await_count == 3


class TestEventsCRUDGetBySlug:
    @pytest.mark.asyncio
    async def test_returns_event_with_participants(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        event_result = MagicMock()
        event_result.first.return_value = (_event_row(), "2026", "West Australian Football League")
        participant_rows = [
            (_event_participant_row(501, 1, "home", 91, True), "Peel Thunder"),
            (_event_participant_row(501, 2, "away", 78, False), "East Fremantle"),
        ]
        participants_result = MagicMock()
        participants_result.all.return_value = participant_rows
        db.execute = AsyncMock(side_effect=[event_result, participants_result])

        result = await EventsCRUD.get_by_slug_with_participants(db, "wafl-abc12345")

        assert result is not None
        assert result["slug"] == "wafl-abc12345"
        assert result["season"] == "2026"
        assert result["competition"] == "West Australian Football League"
        assert len(result["participants"]) == 2
        assert result["participants"][0] == {
            "side": "home",
            "participant_name": "Peel Thunder",
            "score": 91,
            "is_winner": True,
        }

    @pytest.mark.asyncio
    async def test_missing_slug_returns_none(self):
        from packages.shared.crud.events import EventsCRUD

        db = AsyncMock(spec=AsyncSession)
        event_result = MagicMock()
        event_result.first.return_value = None
        db.execute = AsyncMock(return_value=event_result)

        assert await EventsCRUD.get_by_slug_with_participants(db, "nope") is None
