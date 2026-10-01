"""Unit tests for the team identity endpoint (TEAM-IDENTITY 2026-09-30).

Exposes the club identity (crest URL, colours, abbreviation) the
ingestion already captured on the ``teams`` extension rows — the read
link between ``participants`` and the frontend's logo/badge fallbacks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app_with_teams_router(double_mount: bool = False):
    """Construct a minimal FastAPI app with the teams router registered."""
    from app.api.teams import router

    app = FastAPI()
    app.include_router(router, prefix="/api/teams")
    if double_mount:
        # Mirror main.py: the ingress path-trim alias mount.
        app.include_router(router, prefix="/teams", include_in_schema=False)
    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


def _team_rows():
    """Mirror ingestion output: Peel Thunder with full identity."""
    return [
        {
            "name": "Peel Thunder",
            "abbreviation": None,
            "logo_url": "https://cdn.example/peel.png",
            "primary_color": "#000066",
            "secondary_color": "#FFFFFF",
        }
    ]


class TestListTeams:
    def test_returns_envelope_shape(self):
        app = _build_app_with_teams_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=_team_rows()),
        ):
            resp = client.get("/api/teams")

        assert resp.status_code == 200
        body = resp.json()
        assert list(body.keys()) == ["teams"]
        team = body["teams"][0]
        assert team["name"] == "Peel Thunder"
        assert team["abbreviation"] is None
        assert team["logo_url"] == "https://cdn.example/peel.png"
        assert team["primary_color"] == "#000066"
        assert team["secondary_color"] == "#FFFFFF"

    def test_sport_filter_passthrough(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_teams_router()
        _override_db(app, mock_session)
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=[]),
        ) as mock_list:
            resp = client.get("/api/teams?sport=afl")

        assert resp.status_code == 200
        mock_list.assert_awaited_once_with(mock_session, "afl")
        assert resp.json() == {"teams": []}

    def test_no_filter_returns_all_sports(self):
        rows = _team_rows() * 2
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_teams_router()
        _override_db(app, mock_session)
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=rows),
        ) as mock_list:
            resp = client.get("/api/teams")

        assert resp.status_code == 200
        mock_list.assert_awaited_once_with(mock_session, None)
        assert len(resp.json()["teams"]) == 2

    def test_unknown_sport_yields_empty_list_not_404(self):
        """An unknown sport is a filter miss (200, empty list), never a 404."""
        app = _build_app_with_teams_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=[]),
        ):
            resp = client.get("/api/teams?sport=tiddlywinks")

        assert resp.status_code == 200
        assert resp.json() == {"teams": []}

    def test_trailing_slashless_path_resolves(self):
        """The ingress-trim alias (``GET /api/teams`` without the slash)
        must resolve without a redirect — same quirk as sports/games."""
        app = _build_app_with_teams_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=[]),
        ):
            resp = client.get("/api/teams", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.json() == {"teams": []}

    def test_bare_prefix_alias_mount_resolves(self):
        """main.py mounts the router twice (``/api/teams`` + ``/teams``)
        for the production ingress — both must resolve."""
        app = _build_app_with_teams_router(double_mount=True)
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=_team_rows()),
        ):
            resp = client.get("/teams")

        assert resp.status_code == 200
        assert resp.json()["teams"][0]["name"] == "Peel Thunder"

    def test_identity_nulls_are_included(self):
        """A team with no captured identity still appears — with nulls."""
        rows = [
            {
                "name": "Mt Gravatt Vultures",
                "abbreviation": None,
                "logo_url": None,
                "primary_color": None,
                "secondary_color": None,
            }
        ]
        app = _build_app_with_teams_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)

        with patch(
            "packages.shared.crud.multisport.ParticipantCRUD.list_team_identities",
            AsyncMock(return_value=rows),
        ):
            resp = client.get("/api/teams")

        assert resp.status_code == 200
        team = resp.json()["teams"][0]
        assert team["name"] == "Mt Gravatt Vultures"
        assert team["logo_url"] is None
        assert team["primary_color"] is None
        assert team["secondary_color"] is None
