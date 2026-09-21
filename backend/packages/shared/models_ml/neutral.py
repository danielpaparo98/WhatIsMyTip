"""Neutral-venue detection for the models.

The grand final — a season's LAST round — has no true home team: the
``games.home_team`` column is a fixture designation only, and models
that apply a home-advantage bonus (Elo's +50, the home-advantage
model's venue win rates) would otherwise reward the nominal "home"
side for an advantage that does not exist (GF-NEUTRAL, 2026-09-21).

Same heuristic as the round locator and :meth:`MatchReportService.
is_grand_final`: the grand final is the season's maximum ``round_id``.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import Game

logger = get_logger(__name__)


async def is_grand_final(db: AsyncSession, game: Game) -> bool:
    """True when ``game`` sits in its season's last round.

    The grand final is the only fixture that is guaranteed neutral:
    ``home_team`` is the nominally-designated side, but neither team
    receives home-ground advantage.
    """
    if game.round_id is None or game.season is None:
        return False
    result = await db.execute(
        select(func.max(Game.round_id)).where(Game.season == game.season)
    )
    max_round = result.scalar()
    return bool(max_round is not None and game.round_id == max_round)
