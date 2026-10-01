"""FastAPI router for the team identity endpoint (TEAM-IDENTITY 2026-09-30).

Exposes the club crests and colours the ingestion already captured on
the ``teams`` extension rows (migration 0011) so state-league clubs can
render real badges — the read link between ``participants`` and the
frontend's logo/badge fallbacks.

Routes (mounted at ``/api/teams``):

* ``GET /`` — team-kind participants with their identity fields,
  ordered by name; optional ``sport`` filter (unknown values yield an
  empty list, never a 404).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from packages.shared.crud.multisport import ParticipantCRUD
from packages.shared.schemas.teams import (
    TeamIdentityResponse,
    TeamsIdentityResponse,
)

router = APIRouter()


# TEAM-IDENTITY (2026-09-30, user request): the filter (not resource)
# contract — unknown/absent sport values yield 200 + empty list, never 404.
# No-trailing-slash alias so the DigitalOcean ingress (which trims the
# matched `/api` prefix) resolves `/api/teams` directly — see the
# comment on ``list_games`` in games.py.  Hidden from OpenAPI.
@router.get("", response_model=TeamsIdentityResponse, include_in_schema=False)
@router.get("/", response_model=TeamsIdentityResponse)
async def list_teams(
    db: Annotated[AsyncSession, Depends(get_db)],
    sport: Annotated[
        Optional[str],
        Query(description="Filter by sport id (see GET /api/sports); omit for all sports"),
    ] = None,
) -> TeamsIdentityResponse:
    """List team-kind participants with their club identity, ordered by name.

    The club crest source for the frontend: ``logo_url``/colours are
    ``None`` when the feed supplied nothing for a field — the team is
    still returned and the frontend falls back to its badge renderer.
    An unknown ``sport`` value is a filter miss (200, empty list),
    never a 404.
    """
    teams = await ParticipantCRUD.list_team_identities(db, sport)
    return TeamsIdentityResponse(
        teams=[TeamIdentityResponse.model_validate(team) for team in teams]
    )
