"""FastAPI router for the event-scoped public read endpoints (ADR 0001).

The first read-side cutover increment: serves events joined with their
participants from the 0010 events tables, so multi-league data becomes
reachable by the site.  ADDITIVE by design — the legacy ``/api/games``
routes stay untouched (deprecation window per ADR 0001).

Routes (mounted at ``/api/events``):

* ``GET /``        — list events for a competition season (filters:
                      ``competition`` id, ``league`` key, ``season``
                      label, ``round``, ``limit``; 404 when the
                      competition/season is unknown)
* ``GET /{slug}``  — single event with its participants (404 when
                      absent; the slug is globally unique, so no
                      league scoping is needed)

League dispatch (Phase 5.2, additive): ``GET /`` accepts an optional
``league`` query param.  An absent, empty or ``afl`` value rides the
legacy competition-id path unchanged (``competition`` stays required
there, with its original 422 validation semantics); any key known to
the league registries — the ten state leagues AND the rugby-league
national competitions ``nrl``/``nrlw``/``origin`` — is resolved to its
``competitions.id`` (by canonical ``competitions.name``, the same
contract the backtest API uses) so clients can query by league key
without knowing numeric ids.  An unknown key is a 404 in the
repo-standard error shape; a registered-but-never-synced league
degrades to the same 404 as any unknown competition (an event listing
has no graceful zero shape).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import BackendServiceError, http_error
from packages.shared.crud.events import EventsCRUD
from packages.shared.ingestion.national_leagues import get_league
from packages.shared.models import Competition
from packages.shared.schemas.events import (
    EventListResponse,
    EventResponse,
    LeagueEventDetailResponse,
    LeagueMatchReportResponse,
)
from packages.shared.schemas.tips import LeagueTipExplanationResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# League dispatch helpers (Phase 5.2 — mirrors app.api.backtest's
# resolver; both resolve through the same cross-registry facade since
# the backtest guardrail flip)
# ---------------------------------------------------------------------------

#: ``competition_id`` stand-in for a REGISTERED-but-never-synced league:
#: no autoincrement id can be negative, so the events query matches no
#: season row and the existing unknown-competition 404 answers (the
#: same behaviour as an explicit ``competition=999``).
_UNSYNCED_COMPETITION_ID = -1


async def _league_competition_id(db: AsyncSession, league: str) -> int:
    """Resolve a league key to its ``competitions.id``.

    Resolution is by the canonical ``competitions.name`` from the
    cross-registry :func:`get_league` facade (state leagues first, then
    the rugby-league national competitions) — the same names the
    frontend's ``LEAGUE_COMPETITION_NAMES`` mapping pins.

    Raises:
        BackendServiceError: 404 ``not_found`` for a key outside both
            registries (``afl`` never reaches here — the legacy branch
            handles it before dispatch), or when the name resolves to
            multiple competition rows.
    """
    try:
        config = get_league(league)
    except ValueError:
        raise http_error(
            404,
            "not_found",
            f"Unknown league {league!r} — not in the league registries",
        ) from None
    try:
        competition_id = (
            await db.execute(
                select(Competition.id).where(Competition.name == config.name)
            )
        ).scalar_one_or_none()
    except MultipleResultsFound:
        # Uniqueness on competitions is (sport_id, name): a future
        # clashing name must surface as the repo-standard 404, never a
        # latent 500 (mirrors the backtest resolver's Review #5 guard).
        raise http_error(
            404,
            "not_found",
            f"League {league!r} resolved to multiple competitions — "
            "scope the competition registry before querying this league",
        ) from None
    if competition_id is None:
        # Registered but never synced (no competition row yet) — the
        # events query sees no season and the standard 404 answers.
        return _UNSYNCED_COMPETITION_ID
    return int(competition_id)


# The league path resolves ``competition`` from the registry, so the
# query param itself became Optional — but the legacy AFL path must keep
# its ORIGINAL required-int validation semantics with byte-identical 422
# behaviour.  Re-validating through pydantic itself (instead of a
# hand-rolled check) and re-raising as RequestValidationError mirrors
# ``_legacy_season_year`` in app.api.backtest exactly.
_COMPETITION_ID_ADAPTER: TypeAdapter[int] = TypeAdapter(int)


def _require_competition_id(competition: Optional[int]) -> int:
    """Coerce the raw ``competition`` query value for the legacy path."""
    try:
        return _COMPETITION_ID_ADAPTER.validate_python(competition)
    except ValidationError as exc:
        raise RequestValidationError(
            exc.errors(include_url=False, include_context=True)
        ) from None


# No-trailing-slash alias so the DigitalOcean ingress (which trims the
# matched `/api` prefix) resolves `/api/events` directly — see the
# comment on ``list_games`` in games.py.  Hidden from OpenAPI.
@router.get("", response_model=EventListResponse, include_in_schema=False)
@router.get("/", response_model=EventListResponse)
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        str,
        Query(description="Season label, e.g. 2026 (competition-relative)"),
    ],
    competition: Annotated[
        Optional[int],
        Query(
            description="Competition id (see GET /api/sports for ids). "
            "Required unless a resolvable league key is given."
        ),
    ] = None,
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
    league: Annotated[
        Optional[str],
        Query(
            description=(
                "League key (e.g. 'nrl', 'wafl'). Resolves the competition "
                "id from the league registries; absent, empty, or 'afl' "
                "selects the legacy competition-id path."
            )
        ),
    ] = None,
) -> EventListResponse:
    """List events for a competition season, joined with their
    participants (side, name, score, is_winner) and the competition/
    season names.

    ``league`` (additive, Phase 5.2): a registered league key resolves
    the competition id from the registries, so ``?league=nrl&season=2026``
    needs no ``competition`` id.  Absent/empty/``afl`` keeps the legacy
    required-``competition`` behaviour unchanged.

    Raises 404 ``not_found`` when the league, competition or season is
    unknown; a known season with no events returns an empty list.
    """
    if league and league != "afl":
        competition = await _league_competition_id(db, league)
    competition_id = _require_competition_id(competition)
    events = await EventsCRUD.get_by_competition_season(
        db, competition_id, season, round_id, limit=limit
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


@router.get("/{slug}/detail", response_model=LeagueEventDetailResponse)
async def get_event_detail(
    slug: Annotated[str, Path(min_length=1, max_length=16)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LeagueEventDetailResponse:
    """Return an event with all related data (PARITY-1, AFL parity) —
    the league twin of ``GET /api/games/{slug}/detail``: tips (with
    persisted AI explanations), the numeric model breakdown, match
    analysis talking points, and weather.

    Raises 404 ``not_found`` when no event exists for ``slug``.  Every
    section degrades independently: a missing analysis or weather row
    is ``null``, never an error — exactly like the AFL detail.
    """
    from packages.shared.models import (
        LeagueEventWeather,
        LeagueMatchAnalysis,
        LeagueModelPrediction,
    )
    from packages.shared.schemas.events import (
        LeagueMatchAnalysisResponse,
        LeagueModelPredictionSchema,
    )
    from packages.shared.schemas.games import WeatherResponse
    from packages.shared.services.explanation import LeagueTipExplanationService

    event = await EventsCRUD.get_by_slug_with_participants(db, slug)
    if not event:
        raise http_error(404, "not_found", "Event not found")
    event_id = int(event["id"])

    tips = await LeagueTipExplanationService().explanations_for_event(
        db, event_id=event_id
    )

    prediction_rows = (
        await db.execute(
            select(LeagueModelPrediction)
            .where(LeagueModelPrediction.event_id == event_id)
            .order_by(LeagueModelPrediction.id)
        )
    ).scalars().all()
    model_predictions = [
        LeagueModelPredictionSchema(
            model_name=p.model_name,
            winner=p.winner,
            confidence=p.confidence,
            margin=p.margin,
        )
        for p in prediction_rows
    ]

    analysis_row = (
        await db.execute(
            select(LeagueMatchAnalysis).where(
                LeagueMatchAnalysis.event_id == event_id
            )
        )
    ).scalar_one_or_none()
    match_analysis = (
        LeagueMatchAnalysisResponse.model_validate(analysis_row)
        if analysis_row
        else None
    )

    weather_row = (
        await db.execute(
            select(LeagueEventWeather).where(
                LeagueEventWeather.event_id == event_id
            )
        )
    ).scalar_one_or_none()
    weather = (
        WeatherResponse.model_validate(weather_row) if weather_row else None
    )

    return LeagueEventDetailResponse(
        event=EventResponse.model_validate(event),
        tips=[LeagueTipExplanationResponse.model_validate(t) for t in tips],
        model_predictions=model_predictions,
        match_analysis=match_analysis,
        weather=weather,
    )


@router.get("/{slug}/report", response_model=LeagueMatchReportResponse)
async def get_event_report(
    slug: Annotated[str, Path(min_length=1, max_length=16)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LeagueMatchReportResponse:
    """Return the stored grand-final pre-match report for an event
    (PARITY-1) — the league twin of ``GET /api/games/{slug}/report``.

    Raises 404 ``not_found`` when no event exists for ``slug``, when the
    event is not the grand final, or when the report has not been
    generated yet — the exact degradation contract the AFL endpoint
    defines (the frontend treats 404 as "fall back to the detail
    experience").
    """
    from packages.shared.schemas.events import LeagueMatchReportResponse
    from packages.shared.services.league_match_report import (
        LeagueMatchReportService,
    )

    event = await EventsCRUD.get_by_slug_with_participants(db, slug)
    if not event:
        raise http_error(404, "not_found", "Event not found")
    event_id = int(event["id"])

    # Static gate — no OpenRouter client is constructed just to evaluate it.
    if not await LeagueMatchReportService.is_grand_final(db, event_id=event_id):
        raise http_error(
            404, "not_found", "Match report is only available for the grand final"
        )

    # REVIEW-MINOR-2 parity: a storage or validation problem on a
    # read-only cosmetic endpoint degrades to 404 ("not yet generated")
    # instead of a 500 — the frontend already treats 404 as "fall back
    # to the detail experience".
    try:
        row = await LeagueMatchReportService.get_report(db, event_id=event_id)
        if row is None:
            raise http_error(404, "not_found", "Match report not yet generated")
        return LeagueMatchReportResponse.model_validate(row).model_dump(
            mode="json"
        )
    except BackendServiceError:
        raise
    except Exception:
        from packages.shared.logger import get_logger

        get_logger(__name__).exception(
            "League match report unavailable for %s (degrading to 404)", slug
        )
        raise http_error(404, "not_found", "Match report not yet generated")
