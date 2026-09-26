"""Unit tests for the ``/api/sports`` framework endpoints (P4-1).

The multi-sport discovery surface: which sports, competitions, and
seasons the platform serves — backed by the framework tables created
by migration 0010 (ADR 0001).
"""

from __future__ import annotations

import pytest

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app_with_sports_router():
    """Construct a minimal FastAPI app with the sports router registered."""
    from app.api.sports import router

    app = FastAPI()
    app.include_router(router, prefix="/api/sports")
    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


def _sport_rows():
    """Mirror what migration 0010 seeds for the AFL bootstrap."""
    afl = SimpleNamespace(id="afl", display_name="Australian Football")
    comp = SimpleNamespace(
        id=1,
        sport_id="afl",
        name="Australian Football League",
        tier="national",
        format="rounds",
        timezone="Australia/Perth",
    )
    season = SimpleNamespace(
        id=7,
        competition_id=1,
        label="2026",
        start_date=None,
        end_date=None,
        is_current=True,
    )
    return afl, [comp], [season]


class TestListSports:
    def test_returns_afl_bootstrap_shape(self):
        afl, comps, seasons = _sport_rows()
        app = _build_app_with_sports_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.sports.SportsCRUD.list_sports_with_competitions",
            AsyncMock(
                return_value=[
                    {
                        "id": "afl",
                        "display_name": "Australian Football",
                        "competitions": [
                            {
                                "id": 1,
                                "sport_id": "afl",
                                "name": "Australian Football League",
                                "tier": "national",
                                "format": "rounds",
                                "timezone": "Australia/Perth",
                                "seasons": [
                                    {
                                        "id": 7,
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
            ),
        ):
            resp = client.get("/api/sports")

        assert resp.status_code == 200
        body = resp.json()
        assert body["sports"][0]["id"] == "afl"
        competition = body["sports"][0]["competitions"][0]
        assert competition["tier"] == "national"
        assert competition["format"] == "rounds"
        assert competition["seasons"][0]["label"] == "2026"
        assert competition["seasons"][0]["is_current"] is True

    def test_trailing_slashless_path_resolves(self):
        """The ingress-trim alias (``GET /api/sports`` without the slash)
        must resolve without a redirect — same quirk as games."""
        app = _build_app_with_sports_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.sports.SportsCRUD.list_sports_with_competitions",
            AsyncMock(return_value=[]),
        ):
            resp = client.get("/api/sports", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.json() == {"sports": []}


class TestSportsCRUD:
    @pytest.mark.asyncio
    async def test_groups_competitions_and_seasons_under_sports(self):
        from packages.shared.crud.sports import SportsCRUD

        afl, comps, seasons = _sport_rows()
        db = AsyncMock(spec=AsyncSession)

        async def _execute(stmt, *a, **k):
            from unittest.mock import MagicMock

            result = MagicMock()
            # Order of calls: sports, competitions, seasons.
            if not hasattr(_execute, "calls"):
                _execute.calls = 0
            _execute.calls += 1
            if _execute.calls == 1:
                result.scalars.return_value.all.return_value = [afl]
            elif _execute.calls == 2:
                result.scalars.return_value.all.return_value = comps
            else:
                result.scalars.return_value.all.return_value = seasons
            return result

        db.execute = _execute

        sports = await SportsCRUD.list_sports_with_competitions(db)

        assert sports[0]["id"] == "afl"
        competition = sports[0]["competitions"][0]
        assert competition["name"] == "Australian Football League"
        assert competition["seasons"][0]["label"] == "2026"

    @pytest.mark.asyncio
    async def test_empty_tables_yield_empty_list(self):
        from unittest.mock import MagicMock

        from packages.shared.crud.sports import SportsCRUD

        db = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        assert await SportsCRUD.list_sports_with_competitions(db) == []


