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

from datetime import datetime, timedelta
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

        now = datetime.now()
        horizon = now + timedelta(days=_LEAGUE_SWEEP_LOOKAHEAD_DAYS)
        upcoming = (
            (
                await db_execute_upcoming(session, competition_ids, now, horizon)
            ).all()
        )
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
        # report services key on event_id alone.
        for competition_id, event_id in upcoming:
            # Persisted explanations for the four derived heuristics.
            for heuristic in _DERIVED:
                try:
                    text = await explanation_service.generate_explanation_for_tip(
                        session,
                        competition_id=int(competition_id),
                        event_id=int(event_id),
                        heuristic=heuristic,
                    )
                    if text:
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

            # Match-analysis talking points (skip-if-exists inside).
            try:
                if await analysis_service.generate_and_store_analysis(
                    session, event_id=int(event_id)
                ):
                    counts["match_analyses_created"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "league NLP sweep: match analysis failed for event "
                    "%s: %s",
                    event_id,
                    e,
                )

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
    except Exception as e:  # noqa: BLE001 — the sweep never fails the cron
        counts["error"] = str(e)
        logger.error("league NLP sweep failed: %s", e, exc_info=True)
    return counts


async def db_execute_upcoming(
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
