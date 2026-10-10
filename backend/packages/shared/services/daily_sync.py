"""Reusable core for the daily game sync cron job.

Extracted from ``backend/packages/cron/daily-sync/__init__.py`` so that
both the FaaS handler (still in use until Phase 5 deletes it) and the
new in-process :class:`app.cron.daily_sync.DailySyncJob` can share the
same logic.

One pass, two sections (Phase 5.2 multi-sport):

* the AFL (Squiggle) sync — the original behaviour;
* the rugby-league sweep (:func:`run_rugby_league_sync`) — every live
  competition in ``packages.shared.ingestion.national_leagues``
  (``nrl``, ``nrlw``, ``origin``), gated by the rugby-league
  ``SportContext`` (cron timezone ``Australia/Brisbane``, off-season
  Nov–Feb) and failure-isolated per league so a rugby-league outage
  can never break the AFL run in the same job.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import invalidate_cache_pattern, medium_cache
from ..config import settings
from ..logger import get_logger
from ..models_ml.elo import EloModel
from ..sport_context import AFL, RUGBY_LEAGUE, SportContext
from ..squiggle import SquiggleClient
from .game_sync import GameSyncService

logger = get_logger(__name__)


# Off-season hour bounds are now configurable via Settings
# (LO-008).  The local module-level aliases below keep the rest of
# the file readable.
_OFF_SEASON_RUN_START_HOUR = settings.daily_sync_off_season_start_hour
_OFF_SEASON_RUN_END_HOUR = settings.daily_sync_off_season_end_hour


def _is_off_season_skip(now: datetime, context: Optional[SportContext] = None) -> bool:
    """Return True if *now* is in the off-season outside the run window.

    During the sport's off-season the sync only runs inside a 2 AM –
    4 AM window (P3-4: the months come from the SportContext — AFL's
    Oct–Feb is the bootstrap default; a year-round sport's empty set
    never skips).
    """
    from ..sport_context import DEFAULT_CONTEXT

    ctx = context or DEFAULT_CONTEXT
    if not ctx.is_off_season_month(now.month):
        return False
    return now.hour < _OFF_SEASON_RUN_START_HOUR or now.hour >= _OFF_SEASON_RUN_END_HOUR


def is_rugby_league_sync_due(
    now: datetime, *, context: Optional[SportContext] = None
) -> bool:
    """True when the rugby-league pass should run at *now*.

    The gate is evaluated in the sport's OWN cron timezone — the
    rugby-league ``SportContext`` pins ``Australia/Brisbane`` — not the
    app-wide ``settings.cron_timezone`` (Perth).  The two differ in
    exactly the cases this job must get right: October is AFL
    off-season in Perth but rugby-league finals in Brisbane, and the
    2 AM–4 AM reduced window lands two hours earlier on the east
    coast.  A naive *now* is interpreted as the sport timezone's local
    time.
    """
    ctx = context or RUGBY_LEAGUE
    tz = ZoneInfo(ctx.cron_timezone)
    local = now.astimezone(tz) if now.tzinfo is not None else now.replace(tzinfo=tz)
    return not _is_off_season_skip(local, context=ctx)


def rugby_league_sync_leagues() -> List[str]:
    """The rugby-league competitions the daily sync registers: every
    entry in ``NATIONAL_LEAGUES`` whose status starts with ``"live"``.

    The registry is the single source of truth (mirrors
    ``run_all_leagues_sync`` for the state registry), so competitions
    come and go with it without touching this job.
    """
    # Deferred import — the ingestion registry pulls in the sync
    # service modules; keeping that call-time keeps this service's
    # import graph lean (the repo-wide pattern for the ingestion
    # package) and lets tests patch the registry at its source.
    from ..ingestion.national_leagues import NATIONAL_LEAGUES

    return [
        key
        for key, config in NATIONAL_LEAGUES.items()
        if config.status.startswith("live")
    ]


async def run_rugby_league_sync(
    session: AsyncSession,
    *,
    now: Optional[datetime] = None,
    season: Optional[int] = None,
    leagues: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run one rugby-league sweep across the national competitions.

    Cadence & politeness: the sweep rides the daily-sync schedule and
    drives every league through ``run_league_sync`` — the
    FixtureDownload-backed ``NrlProvider`` and its shared 24h TTL cache
    are the ONLY network path, so the pass costs at most one fetch per
    slug per day no matter how often the job fires; the DB work is an
    idempotent upsert.

    Failure contract: one league's failure never aborts the sweep and
    never propagates — the error is logged with its traceback, the
    league's (possibly poisoned) transaction is rolled back, the
    remaining leagues still sync, and the aggregate reports
    ``status="partial"``.

    Args:
        session: An active :class:`AsyncSession`.
        now: Optional override for the current time (tests).  Naive
            values are interpreted in the sport cron timezone.
        season: Optional season override (backfills).  Defaults to
            ``settings.current_season``.
        leagues: Optional explicit league keys (overrides the
            registry-derived live set).

    Returns:
        Aggregate dict (mirrors ``run_all_leagues_sync``): ``status``
        (``"success"`` | ``"partial"`` | ``"off_season_skip"``),
        ``sport``, ``cron_timezone``, ``season``, ``leagues``,
        ``leagues_synced``, ``leagues_failed``, ``fixtures_synced``,
        ``errors``, ``results`` (+ ``message`` on skip).
    """
    tz = ZoneInfo(RUGBY_LEAGUE.cron_timezone)
    local_now = now if now is not None else datetime.now(tz)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=tz)

    year = season if season is not None else settings.current_season
    keys = list(leagues) if leagues is not None else rugby_league_sync_leagues()

    aggregated: Dict[str, Any] = {
        "status": "success",
        "sport": RUGBY_LEAGUE.sport_id,
        "cron_timezone": RUGBY_LEAGUE.cron_timezone,
        "season": year,
        "leagues": keys,
        "leagues_synced": [],
        "leagues_failed": [],
        "fixtures_synced": 0,
        "errors": [],
        "results": {},
    }

    if not is_rugby_league_sync_due(local_now):
        aggregated["status"] = "off_season_skip"
        aggregated["message"] = (
            f"Skipping rugby-league sync \u2013 off-season reduced "
            f"frequency (month={local_now.month}, hour={local_now.hour}, "
            f"tz={RUGBY_LEAGUE.cron_timezone})"
        )
        logger.info(aggregated["message"])
        return aggregated

    # Deferred import (see rugby_league_sync_leagues); patching this
    # runner at its source module is the established test seam.
    from ..ingestion.national_leagues import run_league_sync

    logger.info(
        "Rugby-league daily sync starting: %d leagues (%s) for season %s "
        "in %s (provider cache: at most one fetch per slug per day)",
        len(keys),
        ", ".join(keys),
        year,
        RUGBY_LEAGUE.cron_timezone,
    )

    for key in keys:
        try:
            stats = await run_league_sync(session, key, year)
        except Exception as exc:  # noqa: BLE001 — one league never aborts the sweep
            error = f"{key}: {exc}"
            logger.error("rugby-league sync failed: %s", error, exc_info=True)
            aggregated["leagues_failed"].append(key)
            aggregated["errors"].append(error)
            aggregated["status"] = "partial"
            try:
                # A pass-level failure can leave a poisoned transaction
                # on the shared session — clear it so the remaining
                # leagues (and the AFL section) get a clean one.
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass
            continue
        aggregated["leagues_synced"].append(key)
        aggregated["fixtures_synced"] += stats.get("fixtures_synced", 0)
        aggregated["results"][key] = stats

    logger.info(
        "Rugby-league daily sync completed: %d synced, %d failed, "
        "%d fixtures (%d errors)",
        len(aggregated["leagues_synced"]),
        len(aggregated["leagues_failed"]),
        aggregated["fixtures_synced"],
        len(aggregated["errors"]),
    )
    return aggregated


async def _run_rugby_league_section(
    session: AsyncSession, *, now: datetime
) -> Dict[str, Any]:
    """Enabled-flag gate + last-resort isolation around the sweep.

    The section is TOTAL by contract: whatever happens inside, the AFL
    sync in the same job always proceeds and the job-level
    alerting/retry semantics are unchanged (a rugby-league outage can
    never fail the daily-sync job on its own).
    """
    disabled: Dict[str, Any] = {
        "status": "disabled",
        "sport": RUGBY_LEAGUE.sport_id,
        "leagues": [],
        "leagues_synced": [],
        "leagues_failed": [],
        "fixtures_synced": 0,
        "errors": [],
    }
    if not settings.rugby_league_sync_enabled:
        logger.info("Rugby-league daily sync disabled; skipping section")
        return disabled
    try:
        return await run_rugby_league_sync(session, now=now)
    except Exception:  # noqa: BLE001 — the section must never fail the job
        logger.exception(
            "rugby-league daily sync section failed; continuing with the AFL sync"
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "failed",
            "sport": RUGBY_LEAGUE.sport_id,
            "leagues": [],
            "leagues_synced": [],
            "leagues_failed": [],
            "fixtures_synced": 0,
            "errors": ["rugby-league section failed — see logs"],
        }


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

        - ``status``: ``"success"`` or ``"skipped"`` (the AFL section's
          outcome — a rugby-league failure never downgrades it).
        - ``message``: Human-readable summary (used as the execution's
          ``result_summary``).
        - ``total_games``, ``games_created``, ``games_updated``,
          ``games_skipped``, ``errors``: AFL sync stats (zeroed on skip).
        - ``rugby_league``: The rugby-league sweep aggregate (see
          :func:`run_rugby_league_sync`); ``"disabled"`` when the
          section is switched off.
    """
    tz = ZoneInfo(settings.cron_timezone)
    local_now = now if now is not None else datetime.now(tz)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=tz)

    # Rugby-league pass (Phase 5.2) — runs BEFORE the AFL section and
    # is total (never raises), so the AFL sync below always proceeds
    # and a rugby-league failure can never break it.
    rugby_result = await _run_rugby_league_section(session, now=local_now)

    # The AFL off-season skip is the AFL *context's* gate (Oct–Feb,
    # reduced 2–4 AM window) — it no longer short-circuits the whole
    # job, because October is rugby-league finals month and that
    # sport's own context (Nov–Feb, Brisbane time) keeps its pass
    # alive through the AFL off-season.
    if _is_off_season_skip(local_now, context=AFL):
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
            "rugby_league": rugby_result,
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

        # Update Elo ratings cache after a successful sync — but ONLY
        # when something actually changed.  ``EloModel.update_cache`` is
        # an expensive full-table recompute (it folds every completed
        # game in date order), so skipping it when no games were created
        # or updated avoids the recurring daily-sync spike on the 512 MB
        # instance.  The cache is still invalidated below so reads stay
        # consistent regardless.
        games_changed = games_created > 0 or games_updated > 0
        if games_changed:
            logger.info("Updating Elo ratings cache")
            try:
                await EloModel.update_cache(session)
            except Exception:  # noqa: BLE001
                logger.exception("Elo cache update failed; continuing")
            elo_cache_status = "Elo cache updated"
        else:
            logger.info("Elo cache update skipped: no games changed")
            elo_cache_status = "Elo cache skipped"

        summary_parts = [
            f"Synced {total_games} games for season {season}",
            f"Created: {games_created}, Updated: {games_updated}, Skipped: {games_skipped}",
            elo_cache_status,
        ]
        if error_count > 0:
            summary_parts.append(f"Failed: {error_count}")
        # One-line ops summary of the rugby-league section (never
        # present when the section is disabled/skipped, so the AFL-only
        # message shape is preserved).
        if rugby_result.get("status") not in ("disabled", "off_season_skip"):
            summary_parts.append(
                f"Rugby league ({rugby_result.get('status')}): "
                f"{len(rugby_result.get('leagues_synced', []))} synced, "
                f"{len(rugby_result.get('leagues_failed', []))} failed"
            )
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
            "rugby_league": rugby_result,
        }
    finally:
        await squiggle_client.close()
