"""FastAPI router for the games endpoints.

A thin HTTP adapter over the existing
:mod:`packages.shared.crud.games` and
:mod:`packages.shared.crud.match_analysis` /
:mod:`packages.shared.crud.model_predictions` /
:mod:`packages.shared.crud.tips` modules.  URL paths and response
field names match the FaaS-era ``packages.api.games`` handler 1:1 so
the frontend and existing clients don't change.

Routes (mounted at ``/api/games``):

* ``GET /``              — list games (filters: ``season``, ``round``,
                           ``upcoming``, ``latest``)
* ``GET /{slug}``        — single game by slug
* ``GET /{slug}/detail`` — game + tips + model_predictions +
                           match_analysis + weather
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from packages.shared.config import settings
from packages.shared.crud import (
    GameCRUD,
    MatchAnalysisCRUD,
    ModelPredictionCRUD,
    TipCRUD,
)
from packages.shared.models import Game, MatchWeather
from packages.shared.schemas import (
    GameDetailResponse,
    GameListResponse,
    GameResponse,
    ModelPrediction,
    WeatherResponse,
)
from packages.shared.schemas.match_analysis import MatchAnalysisResponse

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /  — list games
# ---------------------------------------------------------------------------


# Extra no-trailing-slash alias so the DigitalOcean App Platform ingress
# (which trims the matched `/api` prefix) resolves `/api/games` directly
# rather than 307-redirecting to `/games/` (whose Location would drop the
# `/api` prefix and route to the frontend).  Hidden from OpenAPI to avoid
# a duplicate operationId; the `/` form below stays the documented path.
@router.get("", response_model=None, include_in_schema=False)
@router.get("/", response_model=None)
async def list_games(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        Optional[int],
        Query(ge=2000, description="Filter by season year"),
    ] = None,
    round_id: Annotated[
        Optional[int],
        Query(ge=1, alias="round", description="Filter by round number"),
    ] = None,
    upcoming: Annotated[
        bool,
        Query(description="Return only upcoming (incomplete) games"),
    ] = False,
    latest: Annotated[
        bool,
        Query(description="Return the round locator for the next/current round"),
    ] = False,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=500,
            description="Maximum number of games to return (default 50). "
            "Prevents unbounded scans on large seasons.",
        ),
    ] = 50,
):
    """List games, with optional filters.

    When ``latest=true``, returns a small "round locator" object
    (season, round_id, game_count, is_current_year, has_upcoming)
    instead of a games list — used by the homepage to render the
    current round banner.  Otherwise returns a ``GameListResponse``.

    The ``round`` URL parameter matches the FaaS contract (and the
    ``useApi.ts`` frontend helper) verbatim.
    """
    if latest:
        current_year = datetime.now().year
        now = datetime.now(tz=ZoneInfo(settings.cron_timezone)).replace(tzinfo=None)

        # Find the nearest upcoming game
        future_game = await db.execute(
            select(Game.round_id, Game.season)
            .where(and_(Game.date >= now, ~Game.completed))
            .order_by(Game.date.asc())
            .limit(1)
        )
        target = future_game.first()
        has_upcoming = target is not None

        if not target:
            past_game = await db.execute(
                select(Game.round_id, Game.season)
                .where(Game.date < now)
                .order_by(Game.date.desc())
                .limit(1)
            )
            target = past_game.first()

        if target:
            result = await db.execute(
                select(
                    Game.season,
                    Game.round_id,
                    func.count(Game.id).label("game_count"),
                )
                .where(
                    and_(
                        Game.round_id == target.round_id,
                        Game.season == target.season,
                    )
                )
                .group_by(Game.season, Game.round_id)
            )
            row = result.first()
            if row:
                return {
                    "season": row.season,
                    "round_id": row.round_id,
                    "game_count": row.game_count,
                    "is_current_year": row.season == current_year,
                    "has_upcoming": has_upcoming,
                }

        return {
            "season": None,
            "round_id": None,
            "game_count": 0,
            "is_current_year": False,
            "has_upcoming": False,
        }

    # Standard list path — always plumb the `limit` through to the CRUD layer
    # so the SQL is bounded and a single call cannot scan an entire season.
    if upcoming:
        games = await GameCRUD.get_upcoming(db, limit=limit)
    elif season and round_id is not None:
        games = await GameCRUD.get_by_round(db, season, round_id, limit=limit)
    elif season:
        games = await GameCRUD.get_by_season(db, season, limit=limit)
    else:
        games = await GameCRUD.get_upcoming(db, limit=limit)

    resp = GameListResponse(
        games=[GameResponse.model_validate(g) for g in games],
        count=len(games),
    )
    return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /{slug}  — single game
# ---------------------------------------------------------------------------


@router.get("/{slug}")
async def get_game(
    # LO-005: the slug column is VARCHAR(12); the explicit
    # max_length rejects over-long slugs at the routing layer.
    slug: Annotated[str, Path(min_length=1, max_length=12)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return a single game by its public slug identifier.

    Raises 404 ``not_found`` when no game exists for ``slug``.
    """
    game = await GameCRUD.get_by_slug(db, slug)
    if not game:
        raise http_error(404, "not_found", "Game not found")
    return GameResponse.model_validate(game).model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /{slug}/detail  — full game detail
# ---------------------------------------------------------------------------


@router.get("/{slug}/detail")
async def get_game_detail(
    # LO-005: the slug column is VARCHAR(12); the explicit
    # max_length rejects over-long slugs at the routing layer.
    slug: Annotated[str, Path(min_length=1, max_length=12)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return a game with all related data: tips, model predictions,
    match analysis, and weather.

    Raises 404 ``not_found`` when no game exists for ``slug``.
    """
    game = await GameCRUD.get_by_slug(db, slug)
    if not game:
        raise http_error(404, "not_found", "Game not found")

    game_id = game.id

    # Fetch tips, model predictions, match analysis in parallel-ish
    # (sequential awaits are fine; the CRUD methods are short).
    tips = await TipCRUD.get_by_game(db, game_id)
    model_predictions_db = await ModelPredictionCRUD.get_by_game(db, game_id)
    model_predictions = [
        ModelPrediction(
            model_name=p.model_name,
            winner=p.winner,
            confidence=p.confidence,
            margin=p.margin,
        )
        for p in model_predictions_db
    ]
    match_analysis_db = await MatchAnalysisCRUD.get_by_game_id(db, game_id)
    match_analysis = (
        MatchAnalysisResponse.model_validate(match_analysis_db)
        if match_analysis_db
        else None
    )

    # Weather — direct query (no dedicated CRUD module)
    weather_result = await db.execute(
        select(MatchWeather).where(MatchWeather.game_id == game_id)
    )
    weather_row = weather_result.scalar_one_or_none()
    weather = WeatherResponse.model_validate(weather_row) if weather_row else None

    resp = GameDetailResponse(
        game=GameResponse.model_validate(game),
        tips=[t for t in tips],
        model_predictions=model_predictions,
        match_analysis=match_analysis,
        weather=weather,
    )
    return resp.model_dump(mode="json")
