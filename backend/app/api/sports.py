"""FastAPI router for the sports framework endpoints (P4-1).

The multi-sport discovery surface: which sports, competitions, and
seasons the platform serves.  Backed by the framework tables created
by migration ``0010_consolidated_multisport`` (ADR 0001).

Routes (mounted at ``/api/sports``):

* ``GET /`` — list sports, each with its competitions and seasons.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from packages.shared.crud.sports import SportsCRUD
from packages.shared.schemas.sports import SportsListResponse

router = APIRouter()


# No-trailing-slash alias so the DigitalOcean ingress (which trims the
# matched `/api` prefix) resolves `/api/sports` directly — see the
# comment on ``list_games`` in games.py.  Hidden from OpenAPI.
@router.get("", response_model=SportsListResponse, include_in_schema=False)
@router.get("/", response_model=SportsListResponse)
async def list_sports(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SportsListResponse:
    """List all sports with their competitions and seasons.

    The entry point for multi-sport clients: discover the available
    sports, then scope every other query by competition/season.
    """
    sports = await SportsCRUD.list_sports_with_competitions(db)
    return SportsListResponse(sports=sports)
