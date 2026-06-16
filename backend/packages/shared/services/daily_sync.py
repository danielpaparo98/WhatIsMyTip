"""Reusable core for the daily game sync cron job.

Extracted from ``backend/packages/cron/daily-sync/__init__.py`` so that
both the FaaS handler (still in use until Phase 5 deletes it) and the
new in-process :class:`app.cron.daily_sync.DailySyncJob` can share the
same logic.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import invalidate_cache_pattern, medium_cache
from ..config import settings
from ..logger import get_logger
from ..models_ml.elo import EloModel
from .game_sync import GameSyncService
from ..squiggle import SquiggleClient

logger = get_logger(__name__)


# AFL off-season months (October through February)
_OFF_SEASON_MONTHS = {10, 11, 12, 1, 2}
# Off-season 2-4 AM window is when the once-daily sync still runs
_OFF_SEASON_RUN_START_HOUR = 2
_OFF_SEASON_RUN_END_HOUR = 4


def _is_off_season_skip(now: datetime) -> bool:
    """Return True if *now* is in the off-season outside the run window.

    During AFL off-season (Oct-Feb) the sync only runs inside a 2 AM –
    4 AM AWST window.  This is a noise-reduction policy from the FaaS
    implementation (no live games to sync).
    """
    if now.month not in _OFF_SEASON_MONTHS:
        return False
    return now.hour < _OFF_SEASON_RUN_START_HOUR or now.hour >= _OFF_SEASON_RUN_END_HOUR


async def run_daily_sync(
    session: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run a single daily-sync pass.

    Args:
        session: An active :class:`AsyncSession`.
        now: Optional override for the current time (used by tests).
            Defaults to ``datetime.now()`` in ``settings.cron_timezone``.

    Returns:
        A JSON-serialisable result dict:

        - ``status``: ``"success"`` or ``"skipped"``.
        - ``message``: Human-readable summary (used as the execution's
          ``result_summary``).
        - ``total_games``, ``games_created``, ``games_updated``,
          ``games_skipped``, ``errors``: Sync stats (zeroed on skip).
    """
    tz = ZoneInfo(settings.cron_timezone)
    local_now = now if now is not None else datetime.now(tz)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=tz)

    if _is_off_season_skip(local_now):
        msg = (
            f"Skipping daily sync \u2013 off-season reduced frequency "
            f"(month={local_now.month}, hour={local_now.hour})"
        )
        logger.info(msg)
        return {
            "status": "skipped",
            "message": msg,
            "total_games": 0,
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0,
            "errors": 0,
        }

    season = settings.current_season
    squiggle_client = SquiggleClient()
    try:
        sync_service = GameSyncService(
            squiggle_client=squiggle_client,
            db_session=session,
            season=season,
        )

        logger.info("Syncing games from Squiggle API for season %s", season)
        start_time = time.time()
        sync_stats = await sync_service.sync_games()

        games_created = sync_stats.get("games_created", 0)
        games_updated = sync_stats.get("games_updated", 0)
        games_skipped = sync_stats.get("games_skipped", 0)
        total_games = sync_stats.get("total_games", 0)
        error_count = len(sync_stats.get("errors", []))

        # Update Elo ratings cache after successful sync
        logger.info("Updating Elo ratings cache")
        try:
            await EloModel.update_cache(session)
        except Exception:  # noqa: BLE001
            logger.exception("Elo cache update failed; continuing")

        summary_parts = [
            f"Synced {total_games} games for season {season}",
            f"Created: {games_created}, Updated: {games_updated}, Skipped: {games_skipped}",
            "Elo cache updated",
        ]
        if error_count > 0:
            summary_parts.append(f"Failed: {error_count}")
        summary = "; ".join(summary_parts)
        logger.info("daily-sync completed: %s", summary)

        # Invalidate stale cache entries (best-effort)
        try:
            deleted = await invalidate_cache_pattern(medium_cache, "games")
            if deleted > 0:
                logger.info("Cache invalidated: %s games-related entries", deleted)
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": "success",
            "message": summary,
            "total_games": total_games,
            "games_created": games_created,
            "games_updated": games_updated,
            "games_skipped": games_skipped,
            "errors": error_count,
            "duration_seconds": int(time.time() - start_time),
        }
    finally:
        await squiggle_client.close()
