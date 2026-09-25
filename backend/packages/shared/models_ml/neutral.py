"""Neutral-venue detection for the models.

The grand final — a season's LAST round — has no true home team: the
``games.home_team`` column is a fixture designation only, and models
that apply a home-advantage bonus (Elo's learned HA, the
home-advantage model's venue win rates) would otherwise reward the
nominal "home" side for an advantage that does not exist
(GF-NEUTRAL, 2026-09-21).

Same heuristic as the round locator and :meth:`MatchReportService.
is_grand_final`: the grand final is the season's maximum ``round_id`` —
BUT only for a COMPLETE fixture (GF-COMPLETE, 2026-09-25).  Mid-season
the maximum ``round_id`` in the data is merely the latest completed
round, so detection additionally requires the season to show at least
``MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION`` distinct rounds.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import Game

logger = get_logger(__name__)

# GF-COMPLETE (2026-09-25): a complete AFL season spans ~27 distinct
# rounds (23 home-and-away + 4 finals).  A partially-synced in-progress
# season has fewer distinct rounds, so its latest round must NOT be
# treated as the grand final — doing so stripped home advantage from an
# entire normal round (Elo) and made the home-advantage model abstain
# mid-season.  Trade-off: shortened historical seasons (e.g.
# COVID-2020, ~22 rounds) will not get grand-final detection —
# accepted; that is the status quo ante for those seasons.
MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION = 25


async def is_grand_final(db: AsyncSession, game: Game) -> bool:
    """True when ``game`` sits in its season's last round of a COMPLETE
    fixture.

    The grand final is the only fixture that is guaranteed neutral:
    ``home_team`` is the nominally-designated side, but neither team
    receives home-ground advantage.

    A season qualifies as complete only when it has at least
    ``MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION`` distinct rounds — a
    partially-synced in-progress season's max round is just the latest
    completed round, not a final.
    """
    if game.round_id is None or game.season is None:
        return False
    result = await db.execute(
        select(
            func.max(Game.round_id),
            func.count(func.distinct(Game.round_id)),
        ).where(Game.season == game.season)
    )
    max_round, distinct_rounds = result.one()
    return bool(
        max_round is not None
        and game.round_id == max_round
        and distinct_rounds >= MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION
    )
