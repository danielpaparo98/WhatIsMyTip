"""FastAPI router for the event-scoped public read endpoints (ADR 0001).

The first read-side cutover increment: serves events joined with their
participants from the 0010 events tables, so multi-league data becomes
reachable by the site.  ADDITIVE by design — the legacy ``/api/games``
routes stay untouched (deprecation window per ADR 0001).

Routes (mounted at ``/api/events``):

* ``GET /``        — list events for a competition season (filters:
                     ``competition`` id, ``season`` label, ``round``,
                     ``limit``; 404 when the competition/season is
                     unknown)
* ``GET /{slug}``  — single event with its participants (404 when
                     absent)
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from packages.shared.crud.events import EventsCRUD
from packages.shared.schemas.events import EventListResponse, EventResponse

router = APIRouter()


# No-trailing-slash alias so the DigitalOcean ingress (which trims the
# matched `/api` prefix) resolves `/api/events` directly — see the
# comment on ``list_games`` in games.py.  Hidden from OpenAPI.
@router.get("", response_model=EventListResponse, include_in_schema=False)
@router.get("/", response_model=EventListResponse)
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    competition: Annotated[
        int,
        Query(description="Competition id (see GET /api/sports for ids)"),
    ],
    season: Annotated[
        str,
        Query(description="Season label, e.g. 2026 (competition-relative)"),
    ],
    round_id: Annotated[
        Optional[int],
        Query(ge=1, alias="round", description="Filter by round number"),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=500,
            description="Maximum number of events to return (default 100). "
            "Prevents unbounded scans on large seasons.",
        ),
    ] = 100,
) -> EventListResponse:
    """List events for a competition season, joined with their
    participants (side, name, score, is_winner) and the competition/
    season names.

    Raises 404 ``not_found`` when the competition or season is unknown;
    a known season with no events returns an empty list.
    """
    events = await EventsCRUD.get_by_competition_season(
        db, competition, season, round_id, limit=limit
    )
    if events is None:
        raise http_error(404, "not_found", "Competition or season not found")
    return EventListResponse(
        events=[EventResponse.model_validate(e) for e in events],
        count=len(events),
    )


@router.get("/{slug}", response_model=EventResponse)
async def get_event(
    # The slug column is VARCHAR(16); the explicit max_length rejects
    # over-long slugs at the routing layer (mirrors games.py LO-005).
    slug: Annotated[str, Path(min_length=1, max_length=16)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EventResponse:
    """Return a single event by its public slug identifier, with its
    participants nested.

    Raises 404 ``not_found`` when no event exists for ``slug``.
    """
    event = await EventsCRUD.get_by_slug_with_participants(db, slug)
    if not event:
        raise http_error(404, "not_found", "Event not found")
    return EventResponse.model_validate(event)
