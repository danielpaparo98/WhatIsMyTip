"""League NLP sweep — nightly AI content for every league (PARITY-1).

The league counterpart of :func:`.tip_generation._round_nlp_sweep`:
after (or independent of) the AFL pass, every live league competition
gets its upcoming fixtures' AI content back-filled:

* persisted tip explanations for the four derived heuristics (the
  cards the round page renders) — written onto ``league_tips.explanation``;
* match-analysis talking points — written onto ``league_match_analyses``.

Both services skip-if-exists, so the sweep is idempotent and pays the
LLM only for genuinely missing content.  Failure of any single item is
logged and never fails the pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..logger import get_logger
from ..models import Competition, Event, EventParticipant, Season

logger = get_logger(__name__)

#: How far ahead to sweep for fixtures needing AI content.  The AFL
#: sweep is round-scoped; leagues have no shared "current round"
#: notion, so the lookahead window is the equivalent scope.
_LEAGUE_SWEEP_LOOKAHEAD_DAYS = 10

#: R-1 (code review): per-RUN fixture budget.  On the first night after
#: deploy the backlog is 200 fixtures x 5 content pieces x ~5-30 s per
#: LLM call — far past the tip-generation job's 30-minute budget.  A
#: bounded batch finishes inside the cron, and the skip-if-exists
#: contract means every subsequent night drains the remainder until
#: steady state (a handful of fresh fixtures per round).
_LEAGUE_SWEEP_MAX_FIXTURES = 40

#: The derived heuristics the round-page cards render — the only rows
#: that need persisted explanations.
_DERIVED = ("best_bet", "weighted_tip", "boosted_tip", "yolo")


async def _live_competition_ids(db: AsyncSession) -> list[int]:
    """Every competition id in the two live league registries.

    Resolved through the cross-registry ``get_league`` facade by
    canonical competition NAME — the same resolution the syncs seed.
    """
    from ..ingestion.national_leagues import NATIONAL_LEAGUES
    from ..ingestion.state_leagues import STATE_LEAGUES

    names = [
        config.name
        for config in [*STATE_LEAGUES.values(), *NATIONAL_LEAGUES.values()]
        if config.status.startswith("live")
    ]
    if not names:
        return []
    rows = (
        await db.execute(select(Competition.id).where(Competition.name.in_(names)))
    ).all()
    return [int(row[0]) for row in rows]


async def _rollback_quietly(session: AsyncSession) -> None:
    """Clear a poisoned transaction so the sweep's remaining items survive.

    F-1 (code review): a failed statement/commit leaves the AsyncSession
    ABORTED — every later statement dies with PendingRollbackError.  The
    repo's documented discipline (FCL-11 savepoints,
    local_competition_sync._refresh_league_tips, explanation.py's
    write-back) is to roll back in the except path; the rollback itself
    must never raise.
    """
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001 — a failed rollback is logged, not raised
        logger.warning(
            "league NLP sweep: rollback after failure itself failed",
            exc_info=True,
        )


async def run_league_nlp_sweep(session: AsyncSession) -> dict[str, Any]:
    """Back-fill missing AI content for every league's upcoming fixtures.

    Returns ``{"explanations_created": int, "match_analyses_created": int,
    "match_reports_created": int, "error": str | None}`` — per-item
    failures are logged, never raised.
    """
    counts: dict[str, Any] = {
        "explanations_created": 0,
        "match_analyses_created": 0,
        "match_reports_created": 0,
        "error": None,
    }
    try:
        competition_ids = await _live_competition_ids(session)
        if not competition_ids:
            return counts

        # C-2 (code review): a single documented clock convention —
        # naive-UTC now against venue-local ``starts_at``.  Venue-local
        # offsets skew the window by hours (never days), the sweep is
        # idempotent (skip-if-exists) and re-runs nightly, so the
        # boundary slack self-heals; a per-competition tz conversion
        # would re-implement local_competition_sync._to_local for no
        # behavioural gain at this window size.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        horizon = now + timedelta(days=_LEAGUE_SWEEP_LOOKAHEAD_DAYS)
        upcoming = (
            await _fetch_upcoming_fixtures(
                session, competition_ids, now, horizon
            )
        ).all()
        if not upcoming:
            return counts

        from .explanation import LeagueTipExplanationService
        from .league_match_analysis import LeagueMatchAnalysisService
        from .league_match_report import LeagueMatchReportService

        explanation_service = LeagueTipExplanationService()
        analysis_service = LeagueMatchAnalysisService()
        report_service = LeagueMatchReportService()

        # event_id -> (competition_id, event_db_id): the explanation
        # service keys on (competition_id, event_id); the analysis and
        # report services key on event_id alone.  F-3 (code review): the
        # default clients hold httpx pools — the AFL sweep closes its
        # services in a finally, mirrored here.
        try:
            for competition_id, event_id in upcoming[:_LEAGUE_SWEEP_MAX_FIXTURES]:
                # Persisted explanations for the four derived heuristics.
                # SEED-FIX (2026-10-15): count only GENUINELY generated
                # text — persisted-first and cache hits return existing
                # content at zero cost and must not inflate the counter
                # (the drain loop relied on this terminating at zero).
                for heuristic in _DERIVED:
                    try:
                        _text, created = (
                            await explanation_service.ensure_explanation_for_tip(
                                session,
                                competition_id=int(competition_id),
                                event_id=int(event_id),
                                heuristic=heuristic,
                            )
                        )
                        if created:
                            counts["explanations_created"] += 1
                    except Exception as e:  # noqa: BLE001 — one tip never fails the sweep
                        logger.warning(
                            "league NLP sweep: explanation (%s, %s, %s) "
                            "failed: %s",
                            competition_id,
                            event_id,
                            heuristic,
                            e,
                        )
                        await _rollback_quietly(session)

                # Match-analysis talking points (skip-if-exists inside).
                # SEED-FIX: count only genuinely generated text.
                try:
                    _text, created = (
                        await analysis_service.ensure_analysis(
                            session, event_id=int(event_id)
                        )
                    )
                    if created:
                        counts["match_analyses_created"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "league NLP sweep: match analysis failed for event "
                        "%s: %s",
                        event_id,
                        e,
                    )
                    await _rollback_quietly(session)

                # PARITY-1: the grand-final pre-match report (the service's
                # gates skip non-GF events cheaply — one detection query).
                try:
                    if await report_service.generate_and_store_report(
                        session, event_id=int(event_id)
                    ):
                        counts["match_reports_created"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "league NLP sweep: GF report failed for event %s: %s",
                        event_id,
                        e,
                    )
                    await _rollback_quietly(session)
        finally:
            await analysis_service.close()
    except Exception as e:  # noqa: BLE001 — the sweep never fails the cron
        counts["error"] = str(e)
        logger.error("league NLP sweep failed: %s", e, exc_info=True)
    return counts


async def _fetch_upcoming_fixtures(
    session: AsyncSession,
    competition_ids: list[int],
    now: datetime,
    horizon: datetime,
):
    """Upcoming scheduled fixtures (with sides) across the leagues."""
    home_side = aliased(EventParticipant)
    away_side = aliased(EventParticipant)
    stmt = (
        select(Season.competition_id, Event.id)
        .join(home_side, and_(home_side.event_id == Event.id, home_side.side == "home"))
        .join(away_side, and_(away_side.event_id == Event.id, away_side.side == "away"))
        .join(Season, Event.season_id == Season.id)
        .where(
            Season.competition_id.in_(competition_ids),
            Event.status == "scheduled",
            Event.completed.is_(False),
            (Event.starts_at.is_(None))
            | ((Event.starts_at >= now) & (Event.starts_at <= horizon)),
        )
        .order_by(Season.competition_id, Event.starts_at, Event.id)
        .limit(200)
    )
    return await session.execute(stmt)
