"""FastAPI router for the tips endpoints.

A thin HTTP adapter over :mod:`packages.api.tips` that preserves
URL paths and response field names 1:1.

Routes (mounted at ``/api/tips``):

* ``GET  /``                    — list tips (filters: season, round, heuristic, limit)
* ``GET  /games-with-tips``     — games-with-tips for a round (requires season, round)
* ``GET  /{heuristic}``         — tips for one heuristic (``best_bet`` /
                                   ``high_risk_high_reward`` / ``yolo``)
* ``POST /generate``            — public: generate tips for a round
                                   (no auth — intentionally public so any
                                   caller can trigger generation when no
                                   tips exist for a period; rate-limited
                                   to 10/minute per IP)
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from packages.shared.crud import GameCRUD, ModelPredictionCRUD, TipCRUD
from packages.shared.models import Game, Tip
from packages.shared.schemas import (
    ModelPrediction as ModelPredictionSchema,
)
from packages.shared.schemas import (
    TipListResponse,
    TipResponse,
)
from packages.shared.schemas.admin import TipGenerateRequest
from packages.shared.services.tip_generation import TipGenerationService

router = APIRouter()


# Heuristics allow-list (mirrors the FaaS handler).
VALID_HEURISTICS = ["best_bet", "high_risk_high_reward", "yolo"]
_HEURISTIC_PATTERN = r"^(best_bet|high_risk_high_reward|yolo)$"


# Per-route rate limiter for ``POST /generate``: 10 req/min per client
# IP (BACKEND-FAAS-CODE-REVIEW §3.5 + api.md:50).
_post_generate_limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# GET /  — list tips
# ---------------------------------------------------------------------------


@router.get("/")
async def list_tips(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        Optional[int],
        Query(ge=2000, description="Filter by season year"),
    ] = None,
    round_id: Annotated[
        Optional[int],
        Query(ge=1, alias="round", description="Filter by round number"),
    ] = None,
    heuristic: Annotated[
        Optional[str],
        Query(pattern=_HEURISTIC_PATTERN, description="Filter by heuristic"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Max tips to return (heuristic queries)"),
    ] = 100,
):
    """List tips with optional filters.

    * ``season`` + ``round`` → ``get_by_round``
    * ``heuristic``         → ``get_by_heuristic``
    * otherwise             → ``get_by_heuristic('best_bet', limit=50)``
    """
    if season and round_id is not None:
        tips = await TipCRUD.get_by_round(db, season, round_id)
    elif heuristic:
        tips = await TipCRUD.get_by_heuristic(db, heuristic, limit=limit)
    else:
        tips = await TipCRUD.get_by_heuristic(db, "best_bet", limit=50)

    resp = TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )
    return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /games-with-tips  — requires season + round
# ---------------------------------------------------------------------------


@router.get("/games-with-tips")
async def games_with_tips(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        int,
        Query(ge=2000, description="Season year (required)"),
    ],
    round_id: Annotated[
        int,
        Query(ge=1, alias="round", description="Round number (required)"),
    ],
    heuristic: Annotated[
        str,
        Query(
            pattern=_HEURISTIC_PATTERN,
            description="Heuristic to attach a tip for",
        ),
    ] = "best_bet",
):
    """Return games for a round with their tips (if generated) and
    model predictions.

    Both ``season`` and ``round`` are required; FastAPI returns 422
    automatically when they are missing.
    """
    # Lock games for this round to prevent concurrent tip generation.
    async with db.begin():
        stmt = (
            select(Game)
            .where(
                Game.season == season,
                Game.round_id == round_id,
            )
            .with_for_update()
        )
        games_result = await db.execute(stmt)
        games = list(games_result.scalars().all())

        if not games:
            return {"games": [], "count": 0}

        game_ids = [g.id for g in games]

        if heuristic:
            result = await db.execute(
                select(Tip).where(
                    Tip.game_id.in_(game_ids),
                    Tip.heuristic == heuristic,
                )
            )
        else:
            result = await db.execute(
                select(Tip).where(Tip.game_id.in_(game_ids))
            )
        tips = list(result.scalars().all())

    tips_by_game = {tip.game_id: tip for tip in tips}
    predictions_by_game = await ModelPredictionCRUD.get_by_games(db, game_ids)

    model_predictions_by_game: dict[int, list[ModelPredictionSchema]] = {}
    for gid, predictions_db in predictions_by_game.items():
        model_predictions_by_game[gid] = [
            ModelPredictionSchema(
                model_name=p.model_name,
                winner=p.winner,
                confidence=p.confidence,
                margin=p.margin,
            )
            for p in predictions_db
        ]

    games_with_tips_payload: list[dict] = []
    for game in games:
        # ``int(...)`` stripper required by pylance — Column vs int
        gid = int(game.id)  # type: ignore[arg-type]
        game_dict = {
            "id": gid,
            "slug": game.slug,
            "squiggle_id": game.squiggle_id,
            "round_id": game.round_id,
            "season": game.season,
            "home_team": game.home_team,
            "away_team": game.away_team,
            "home_score": game.home_score,
            "away_score": game.away_score,
            "venue": game.venue,
            "date": game.date.isoformat() if game.date is not None else None,
            "completed": game.completed,
            "tip": None,
            "model_predictions": model_predictions_by_game.get(gid, []),
        }

        if gid in tips_by_game:
            tip = tips_by_game[gid]
            game_dict["tip"] = {
                "id": tip.id,
                "heuristic": tip.heuristic,
                "selected_team": tip.selected_team,
                "margin": tip.margin,
                "confidence": tip.confidence,
                "explanation": tip.explanation,
                "created_at": tip.created_at.isoformat()
                if tip.created_at is not None
                else None,
            }

        games_with_tips_payload.append(game_dict)

    return {"games": games_with_tips_payload, "count": len(games_with_tips_payload)}


# ---------------------------------------------------------------------------
# GET /{heuristic}
# ---------------------------------------------------------------------------


@router.get("/{heuristic}")
async def tips_by_heuristic(
    heuristic: Annotated[
        str,
        Path(
            pattern=_HEURISTIC_PATTERN,
            description="Heuristic name (best_bet, high_risk_high_reward, yolo)",
        ),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Max tips to return"),
    ] = 100,
):
    """Return tips for a single heuristic (latest first, capped by ``limit``)."""
    # Path-level pattern validation already rejects unknown heuristics
    # with 422, but keep a defensive check.
    if heuristic not in VALID_HEURISTICS:
        raise http_error(
            422,
            "invalid_heuristic",
            f"Invalid heuristic. Must be one of: {', '.join(VALID_HEURISTICS)}",
        )

    tips = await TipCRUD.get_by_heuristic(db, heuristic, limit=limit)
    resp = TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )
    return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# POST /generate  — public, rate-limited
# ---------------------------------------------------------------------------


# Intentionally public — rate-limited to 10/min per IP. See docs/api.md.
@router.post("/generate")
@_post_generate_limiter.limit("10/minute")
async def generate_tips(
    request: Request,
    body: Annotated[TipGenerateRequest, Body(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Generate tips for a specific round.

    **Intentionally public.**  No ``X-API-Key`` is required: any caller
    may trigger tip generation for a season/round that has no tips yet.
    Protection is the per-IP rate limit of 10 requests/minute.
    """
    season = body.season
    round_id = body.round_id
    regenerate = body.regenerate

    # Validate heuristics if provided
    if body.heuristics:
        invalid = [h for h in body.heuristics if h not in VALID_HEURISTICS]
        if invalid:
            raise http_error(
                422,
                "invalid_heuristics",
                f"Invalid heuristic(s): {', '.join(invalid)}. "
                f"Must be one of: {', '.join(VALID_HEURISTICS)}",
            )

    # round_id is required for on-demand generation.  FastAPI's body
    # validation (TipGenerateRequest) only requires ``season``; we
    # re-validate ``round_id`` here to keep the FaaS contract.
    if round_id is None:
        raise http_error(
            422,
            "validation_error",
            "Both 'season' and 'round_id' are required",
        )

    # Look up games for the round
    games = await GameCRUD.get_by_round(db, season, round_id)
    if not games:
        raise http_error(
            404,
            "not_found",
            f"No games found for season {season}, round {round_id}",
        )

    # Run generation via the existing service.
    generation_service = TipGenerationService(
        db_session=db,
        season=season,
        round_id=round_id,
    )
    stats = await generation_service.generate_for_round(
        season=season,
        round_id=round_id,
        regenerate=regenerate,
    )

    return {
        "status": "success",
        "season": season,
        "round_id": round_id,
        "games_processed": stats["games_processed"],
        "tips_created": stats["tips_created"],
        "tips_skipped": stats["tips_skipped"],
        "tips_updated": stats.get("tips_updated", 0),
        "errors": stats.get("errors", []),
    }
