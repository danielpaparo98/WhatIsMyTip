"""Historical backfill loader — rugby-league seasons 2017+ (Phase 5.2).

One entry point, :func:`run_nrl_historic_load`, backfills full history
for the three owner-approved competitions (``nrl``, ``nrlw``,
``origin``) into the SAME generic multisport tables the live sync uses:
every (competition, season) pass is literally
:func:`.national_leagues.run_league_sync` — ``NrlProvider`` → canonical
``FixtureDTO`` → ``LocalCompetitionSyncService``.  There is no parallel
schema and no CSV parser: the FixtureDownload JSON feed serves history
(2017+ verified live, 2026-10-09), so backtest rows and live rows are
one and the same shape.

Guarantees:

* **Venue normalization AT LOAD** — the provider resolves every
  sponsor-branded ``Location`` through :mod:`.ingestion.venue_aliases`
  before storage, so the Sharks' Cronulla ground lands as ``Shark Park``
  whether the source season says Southern Cross Group Stadium (2017),
  PointsBet Stadium (2022) or Ocean Protect Stadium (2026).  Backtest
  grouping is therefore venue-stable across sponsor drift.

* **Idempotent re-load** — events upsert in place via
  ``EventCRUD.upsert_fixture`` (source-ref fast path); re-running a
  backfill updates and never duplicates.

* **Historical hygiene** — backfilled seasons are NOT marked current
  (``mark_current=False`` default): only the live season carries
  ``seasons.is_current``.

* **Loud refusals, no silent scope creep** — anything but the
  sanctioned FixtureDownload source (Kaggle datasets 1990+, the
  not-built nrl.com fallback), any pre-2017 season, any unknown
  competition, or an empty request raises ``ValueError`` BEFORE the
  first pass runs.

* **One dead pass never aborts the sweep** — a missing season payload
  (e.g. ``nrlw`` years before the competition existed) is recorded on
  ``stats["errors"]`` and the remaining passes continue
  (``status="partial"``, or ``"failed"`` when nothing syncs).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..ingestion.league_seeding import MIN_SEASON, season_years
from ..ingestion.national_leagues import NATIONAL_LEAGUES, run_league_sync
from ..logger import get_logger

logger = get_logger(__name__)

#: The ONLY sanctioned history source.  The provider slugs and the
#: payload dialect are FixtureDownload's; the refusal messages name the
#: out-of-scope alternatives explicitly (Kaggle deep history, the
#: not-built nrl.com fallback) so an operator reading the traceback
#: knows the decision was deliberate — see docs/data-loading.md.
SUPPORTED_SOURCE = "fixturedownload"

#: The three owner-approved competitions (2026-10-09), in backfill order.
APPROVED_COMPETITIONS: Tuple[str, ...] = ("nrl", "nrlw", "origin")


def backfill_seasons(through_year: Optional[int] = None) -> List[int]:
    """The seasons a full backfill covers: ``MIN_SEASON`` (2017) through
    ``through_year`` (default: the configured current season).

    ``through_year`` before 2017 raises via
    :func:`.league_seeding.season_years` — the loud deep-history refusal.
    """
    return season_years(
        through_year if through_year is not None else settings.current_season
    )


def _validate_source(source: str) -> None:
    """Refuse anything but the sanctioned FixtureDownload feed — loudly,
    before any pass runs (Kaggle 1990+ / nrl.com are out of scope)."""
    if source != SUPPORTED_SOURCE:
        raise ValueError(
            f"Unsupported historical source {source!r}: the backfill "
            f"loader only loads {SUPPORTED_SOURCE!r} (the FixtureDownload "
            "JSON feed). Kaggle datasets (1990+) are out of scope, and "
            "the nrl.com fallback is documented as NOT built — see "
            "docs/data-loading.md."
        )


def _validate_competitions(competitions: Sequence[str]) -> List[str]:
    """Validate and order the requested competitions (approved order,
    deduplicated).  Unknown keys and empty requests refuse loudly."""
    if not competitions:
        raise ValueError(
            "No competitions requested — pass at least one of "
            f"{list(APPROVED_COMPETITIONS)}."
        )
    unknown = sorted({key for key in competitions if key not in NATIONAL_LEAGUES})
    if unknown:
        raise ValueError(
            f"Unknown competition(s) {unknown} — approved: "
            f"{list(APPROVED_COMPETITIONS)}"
        )
    wanted: Set[str] = set(competitions)
    return [key for key in APPROVED_COMPETITIONS if key in wanted]


def _validate_seasons(
    seasons: Optional[Sequence[int]], through_year: Optional[int]
) -> List[int]:
    """Resolve the season list (explicit, else the 2017+ window) and
    refuse anything before the first supported season."""
    if seasons is None:
        return backfill_seasons(through_year)
    season_list = sorted({int(season) for season in seasons})
    if not season_list:
        raise ValueError("No seasons requested — pass at least one season.")
    early = [season for season in season_list if season < MIN_SEASON]
    if early:
        raise ValueError(
            f"Season(s) {early} predate the first supported rugby-league "
            f"season ({MIN_SEASON}) — deeper history (e.g. Kaggle 1990+) "
            "is out of scope."
        )
    return season_list


async def run_nrl_historic_load(
    session: AsyncSession,
    *,
    competitions: Sequence[str] = APPROVED_COMPETITIONS,
    through_year: Optional[int] = None,
    seasons: Optional[Sequence[int]] = None,
    source: str = SUPPORTED_SOURCE,
    mark_current: bool = False,
) -> Dict[str, Any]:
    """Backfill historical seasons for the rugby-league competitions.

    Drives :func:`.national_leagues.run_league_sync` — the live-sync
    runner — once per (competition, season); validation happens up
    front so an out-of-scope request never touches the database.

    Args:
        session: Active :class:`AsyncSession`.
        competitions: Subset of ``nrl|nrlw|origin`` (default: all three).
        through_year: Last season when ``seasons`` is not given.
        seasons: Explicit season list (overrides ``through_year``);
            duplicates are collapsed, order normalized ascending.
        source: Data source — only ``"fixturedownload"`` is sanctioned.
        mark_current: Passed through to the runner; the historical
            default is ``False`` (backfilled seasons are never current).

    Returns:
        An aggregate stats dict::

            {
                "status": "success" | "partial" | "failed",
                "source": "fixturedownload",
                "competitions": [...],       # attempted, approved order
                "seasons": [...],            # attempted, ascending
                "passes_attempted": int,
                "passes_synced": int,
                "passes_failed": int,
                "fixtures_synced": int,      # across all passes
                "errors": ["<league> <season>: <reason>", ...],
                "results": {league: {season: run_league_sync stats}},
            }
    """
    _validate_source(source)
    league_keys = _validate_competitions(competitions)
    season_list = _validate_seasons(seasons, through_year)

    total_passes = len(league_keys) * len(season_list)
    logger.info(
        "Starting rugby-league historic load: %s × seasons %s..%s "
        "(%d pass(es), source=%s, mark_current=%s)",
        league_keys,
        season_list[0],
        season_list[-1],
        total_passes,
        source,
        mark_current,
    )

    results: Dict[str, Dict[int, Dict[str, Any]]] = {}
    errors: List[str] = []
    fixtures_synced = 0
    synced_passes = 0

    for league_key in league_keys:
        results[league_key] = {}
        for season in season_list:
            try:
                stats = await run_league_sync(
                    session, league_key, season, mark_current=mark_current
                )
            except Exception as exc:  # noqa: BLE001 — one dead pass never aborts the sweep
                # Discard the failed pass's uncommitted partial writes so
                # the next pass starts from a clean transaction.
                await session.rollback()
                error = f"{league_key} {season}: {exc}"
                logger.error("backfill pass failed: %s", error, exc_info=True)
                errors.append(error)
                continue
            results[league_key][int(season)] = stats
            fixtures_synced += int(stats.get("fixtures_synced", 0))
            synced_passes += 1
            logger.info(
                "backfill pass ok: %s %s (%s fixtures)",
                league_key,
                season,
                stats.get("fixtures_synced", 0),
            )

    if synced_passes == total_passes:
        status = "success"
    elif synced_passes == 0:
        status = "failed"
    else:
        status = "partial"

    summary = {
        "status": status,
        "source": SUPPORTED_SOURCE,
        "competitions": league_keys,
        "seasons": season_list,
        "passes_attempted": total_passes,
        "passes_synced": synced_passes,
        "passes_failed": total_passes - synced_passes,
        "fixtures_synced": fixtures_synced,
        "errors": errors,
        "results": results,
    }
    logger.info(
        "Rugby-league historic load finished: %s — %d/%d pass(es), "
        "%d fixtures, %d error(s)",
        status,
        synced_passes,
        total_passes,
        fixtures_synced,
        len(errors),
    )
    return summary


__all__ = [
    "APPROVED_COMPETITIONS",
    "SUPPORTED_SOURCE",
    "backfill_seasons",
    "run_nrl_historic_load",
]
