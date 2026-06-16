"""Reusable core for the historic-data refresh cron job.

Extracted from ``backend/packages/cron/historic-refresh/__init__.py`` so
that both the FaaS handler (still in use until Phase 5 deletes it) and
the new in-process :class:`app.cron.historic_refresh.HistoricRefreshJob`
can share the same logic.

The job processes seasons in batches of ``BATCH_SIZE`` with a hard time
budget of ``MAX_RUNTIME_SECONDS``.  When the budget is exhausted, the
remaining seasons are stored in Redis so the next invocation can resume.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..alerting import AlertingService
from ..cache import RedisCache, invalidate_cache_pattern, medium_cache
from ..config import settings
from ..crud.generation_progress import GenerationProgressCRUD
from ..logger import get_logger
from .historic_data_refresh import HistoricDataRefreshService

logger = get_logger(__name__)


# All seasons covered by the historic refresh
ALL_SEASONS: List[int] = list(range(2010, 2026))

# Number of seasons per batch — finer granularity keeps each batch short
# within the 15-minute limit.
BATCH_SIZE: int = 2

# Redis key for continuation state (prefix "wimt:" is added by RedisCache).
CONTINUATION_KEY: str = "historic-refresh:remaining"

# TTL for the continuation marker — 1 week is plenty for a weekly job.
CONTINUATION_TTL: int = 7 * 24 * 3600

# Maximum runtime before yielding to avoid exceeding DO Functions timeout.
# 13 minutes leaves a 2-minute safety buffer under the 15-minute hard limit.
MAX_RUNTIME_SECONDS: int = 780


def _build_batches(seasons: Optional[List[int]] = None) -> List[List[int]]:
    """Split *seasons* into batches of ``BATCH_SIZE``."""
    source = seasons if seasons is not None else ALL_SEASONS
    return [source[i : i + BATCH_SIZE] for i in range(0, len(source), BATCH_SIZE)]


async def run_historic_refresh(
    session: AsyncSession,
    *,
    cache: Optional[RedisCache] = None,
    alerting: Optional[AlertingService] = None,
) -> Dict[str, Any]:
    """Run a single historic-data refresh pass.

    Process seasons in batches with a hard time budget.  When the
    budget is exhausted, remaining seasons are stored in Redis for
    the next invocation.

    Args:
        session: Active :class:`AsyncSession`.
        cache: Optional :class:`RedisCache` (created on demand if not
            supplied).
        alerting: Optional :class:`AlertingService` (created on demand).

    Returns:
        A result dict with:
        - ``status``: ``"success"`` (or ``"failed"`` on a batch error).
        - ``message``: human-readable summary.
        - ``batches_processed``, ``total_seasons_processed``,
          ``total_games_synced``, ``total_tips_generated``,
          ``total_errors``.
        - ``timed_out``: ``True`` if the time budget was exhausted.
    """
    cache = cache or RedisCache()
    alerting = alerting or AlertingService()

    # 1. Determine seasons to process.
    #    Priority:  (1) Redis fast-path continuation marker,
    #               (2) DB `generation_progress` table fallback,
    #               (3) Fresh start with all seasons.
    remaining = await cache.get(CONTINUATION_KEY)
    if remaining:
        seasons_to_process = remaining
        logger.info(
            "Resuming from Redis continuation marker: %s seasons remaining",
            len(seasons_to_process),
        )
    else:
        active_ops = await GenerationProgressCRUD.get_in_progress_operations(
            session, operation_type="historic_refresh"
        )
        db_progress = active_ops[0] if active_ops else None
        if db_progress and db_progress.completed_items:
            already_done = db_progress.completed_items
            remaining_indices = list(range(already_done, len(ALL_SEASONS)))
            seasons_to_process = [ALL_SEASONS[i] for i in remaining_indices]
            logger.info(
                "Resuming from DB progress (record %s): %s seasons already done, %s remaining",
                db_progress.id,
                already_done,
                len(seasons_to_process),
            )
        else:
            seasons_to_process = list(ALL_SEASONS)
            logger.info(
                "Starting fresh: processing all %s seasons",
                len(seasons_to_process),
            )

    batches = _build_batches(seasons_to_process)

    # 2. Process batches with time check
    overall_start = time.time()
    total_seasons_processed = 0
    total_games_synced = 0
    total_tips_generated = 0
    total_errors = 0
    batches_processed = 0
    timed_out = False

    for batch_idx, seasons in enumerate(batches):
        elapsed = time.time() - overall_start
        if elapsed > MAX_RUNTIME_SECONDS:
            # Store remaining seasons in Redis for next invocation
            remaining_seasons: list[int] = []
            for remaining_batch in batches[batch_idx:]:
                remaining_seasons.extend(remaining_batch)
            await cache.set(
                CONTINUATION_KEY,
                remaining_seasons,
                ttl=CONTINUATION_TTL,
            )
            logger.warning(
                "Approaching timeout after %.0fs, processed %s batch(es). "
                "Stored %s remaining seasons in Redis.",
                elapsed,
                batches_processed,
                len(remaining_seasons),
            )
            try:
                await alerting.send_timeout_alert(
                    job_name="historic-refresh",
                    elapsed_seconds=elapsed,
                    remaining_work=f"{len(remaining_seasons)} seasons remaining",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to send timeout alert")
            timed_out = True
            break

        seasons_str = ",".join(str(s) for s in seasons)

        logger.info(
            "Processing batch %s/%s: %s",
            batch_idx + 1,
            len(batches),
            seasons_str,
        )

        batch_start = time.time()
        refresh_service = HistoricDataRefreshService(
            db_session=session,
            seasons=seasons,
            round_id=None,
            regenerate_tips=settings.historic_refresh_regenerate_tips,
        )
        refresh_stats = await refresh_service.refresh_from_string(
            seasons_str=seasons_str,
            round_id=None,
            regenerate_tips=settings.historic_refresh_regenerate_tips,
        )

        batch_duration = time.time() - batch_start
        total_seasons_processed += refresh_stats["seasons_processed"]
        total_games_synced += refresh_stats["games_synced"]
        total_tips_generated += refresh_stats["tips_generated"]
        total_errors += len(refresh_stats.get("errors", []))
        batches_processed += 1

        # Persist completed count to generation_progress so the next
        # invocation can resume even if Redis is wiped.
        try:
            await GenerationProgressCRUD.upsert_active(
                session,
                operation_type="historic_refresh",
                total_items=len(ALL_SEASONS),
                completed_items=total_seasons_processed,
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to persist DB continuation marker", exc_info=True)

        logger.info(
            "Batch %s completed in %.1fs: %s seasons, %s games, %s tips",
            seasons_str,
            batch_duration,
            refresh_stats["seasons_processed"],
            refresh_stats["games_synced"],
            refresh_stats["tips_generated"],
        )

    # If all batches were processed, clear both continuation markers
    if batches_processed == len(batches):
        await cache.delete(CONTINUATION_KEY)
        try:
            active_ops = await GenerationProgressCRUD.get_in_progress_operations(
                session, operation_type="historic_refresh"
            )
            for op in active_ops:
                await GenerationProgressCRUD.mark_completed(
                    session, op.id, completed_items=len(ALL_SEASONS)
                )
            await session.commit()
            if active_ops:
                logger.info("DB continuation marker(s) marked completed")
        except Exception:  # noqa: BLE001
            logger.warning("Failed to clear DB continuation marker", exc_info=True)
        logger.info("All seasons processed, continuation markers cleared")

    # Build summary
    overall_duration = time.time() - overall_start
    summary_parts = [
        f"Processed {total_seasons_processed} seasons across {batches_processed} batch(es)",
        f"Synced {total_games_synced} games",
        f"Generated {total_tips_generated} tips",
    ]
    if total_errors > 0:
        summary_parts.append(f"Failed: {total_errors} season(s)")
    if timed_out:
        summary_parts.append("Timeout: continuation marker stored")
    summary = "; ".join(summary_parts)
    logger.info("historic-refresh completed: %s", summary)

    # Full cache invalidation after historic data refresh (best-effort)
    try:
        await invalidate_cache_pattern(medium_cache, "games")
        await invalidate_cache_pattern(medium_cache, "tips")
        await invalidate_cache_pattern(medium_cache, "backtest")
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "success",
        "message": summary,
        "batches_processed": batches_processed,
        "total_seasons_processed": total_seasons_processed,
        "total_games_synced": total_games_synced,
        "total_tips_generated": total_tips_generated,
        "total_errors": total_errors,
        "timed_out": timed_out,
        "duration_seconds": int(overall_duration),
    }
