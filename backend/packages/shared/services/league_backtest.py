"""League backtest query service (performance-per-league D4).

The league-generic counterpart of :mod:`packages.shared.services.backtest`
(which stays AFL-only and is NOT modified by this module).  Philosophy
parity with the AFL service: correctness is graded at QUERY time from
``Event``/``EventParticipant`` results — nothing correctness-shaped is
stored — and profit reuses :mod:`packages.shared.services.settlement`
exactly ($10 stake, real decimal odds where available, the
representative $1.90 fallback otherwise, draws push).  State leagues
have no odds source today, so ``odds_coverage`` is 0 and every tip
settles at the fallback price — the expected, honestly-surfaced shape.

Payload shapes mirror the AFL response payloads so the frontend renders
ONE presentation for both paths:

* comparison stats = the AFL ``calculate_backtest_from_tips`` dict
  (``total_rounds``/``total_tips``/``total_correct``/
  ``overall_accuracy``/``total_profit``/``avg_profit_per_round``/
  ``best_round_accuracy``/``worst_round_accuracy``/``odds_coverage``);
* current season = the AFL ``CurrentSeasonResponse`` shape with the
  season label stringified (league season labels are
  competition-relative strings, not calendar-year ints);
* seasons = the D4 ``{available_years, current_year}`` payload with
  string labels, latest first.

BT-ROUND semantics are mirrored from
``BacktestService.get_current_season_performance``: ``total_rounds``
derives from the season fixture's distinct ``round_id`` values, and
``rounds_completed`` counts rounds where EVERY event is completed.
One deliberate deviation: no synthetic 24-round fallback — a league
with no rounds loaded reports 0 and projects $0 rather than inventing
an AFL-shaped fixture size.

Boundary contract: lookups are ``competition_id``-based; the API layer
(subtask 05) resolves the frontend's league key to a competition via
the ``LEAGUE_COMPETITION_NAMES`` ↔ ``competitions.name`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypedDict

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..models import Event, EventParticipant, LeagueTip, Season
from .league_heuristics import LEAGUE_HEURISTICS, SeasonRef
from .settlement import is_valid_decimal_odds, settle_stake

# ---------------------------------------------------------------------------
# Payload shapes (mirrors of the AFL response models, string-labelled).
# ---------------------------------------------------------------------------


class SeasonStats(TypedDict):
    """One heuristic's season stats — the AFL comparison-dict shape."""

    total_rounds: int
    total_tips: int
    total_correct: int
    overall_accuracy: float
    total_profit: float
    avg_profit_per_round: float
    best_round_accuracy: float
    worst_round_accuracy: float
    odds_coverage: float


class BestOverall(TypedDict):
    """The AFL ``/compare`` ``best_overall`` summary shape."""

    heuristic: str | None
    accuracy: float
    profit: float


class SeasonComparison(TypedDict):
    """The AFL ``GET /compare`` response shape, string-labelled."""

    season: str | None
    comparison: dict[str, SeasonStats]
    best_overall: BestOverall


class AvailableSeasons(TypedDict):
    """The D4 seasons payload — labels, latest first."""

    available_years: list[str]
    current_year: str | None


class HeuristicSeasonPerformance(TypedDict):
    """One heuristic's current-season entry — the AFL
    ``CurrentSeasonHeuristicPerformance`` shape."""

    heuristic: str
    total_profit: float
    total_accuracy: float
    rounds_played: int
    avg_profit_per_round: float
    projected_annual_profit: float
    odds_coverage: float


class CurrentSeasonPerformance(TypedDict):
    """The AFL ``CurrentSeasonResponse`` shape, string-labelled."""

    season: str | None
    heuristics: list[HeuristicSeasonPerformance]
    rounds_completed: int
    total_rounds: int


# ---------------------------------------------------------------------------
# Pure grading — query-time correctness from event results.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GradedTip:
    """One league tip joined to its event result, graded.

    ``decimal_odds`` is the tipped side's real price when an odds source
    exists; ``None`` settles at ``FALLBACK_DECIMAL_ODDS`` (state leagues
    today — ``odds_coverage`` 0, by design).
    """

    round_id: int | None
    is_correct: bool
    is_draw: bool
    decimal_odds: float | None


def winning_side_id(
    home_side_id: int | None,
    away_side_id: int | None,
    home_score: int | None,
    away_score: int | None,
) -> int | None:
    """Winning ``event_participants.id``, or ``None`` on a draw/no result.

    A drawn game has no winner — a tip on either side is incorrect (the
    AFL ``actual_winner_name`` semantics, ported to side ids).
    """
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return home_side_id
    if away_score > home_score:
        return away_side_id
    return None


def is_draw_score(home_score: int | None, away_score: int | None) -> bool:
    """Both scores present and equal — a completed, drawn event."""
    return (
        home_score is not None and away_score is not None and home_score == away_score
    )


def grade_tip(
    *,
    round_id: int | None,
    home_side_id: int | None,
    away_side_id: int | None,
    home_score: int | None,
    away_score: int | None,
    selected_side_id: int | None,
    decimal_odds: float | None = None,
) -> GradedTip:
    """Grade one tip against its event result (pure, no persistence)."""
    return GradedTip(
        round_id=round_id,
        is_correct=(
            selected_side_id is not None
            and selected_side_id
            == winning_side_id(
                home_side_id, away_side_id, home_score, away_score
            )
        ),
        is_draw=is_draw_score(home_score, away_score),
        decimal_odds=decimal_odds,
    )


def round_accuracy_values(tips: Sequence[GradedTip]) -> list[float]:
    """Accuracy per round (tips grouped by non-null ``round_id``),
    ascending — the denominator/ extremes source for the stats dict."""
    totals: dict[int, int] = {}
    correct: dict[int, int] = {}
    for tip in tips:
        if tip.round_id is None:
            continue
        totals[tip.round_id] = totals.get(tip.round_id, 0) + 1
        if tip.is_correct:
            correct[tip.round_id] = correct.get(tip.round_id, 0) + 1
    return [correct.get(round_id, 0) / totals[round_id] for round_id in sorted(totals)]


def _zero_stats() -> SeasonStats:
    """The well-defined zero structure the empty state passes through."""
    return {
        "total_rounds": 0,
        "total_tips": 0,
        "total_correct": 0,
        "overall_accuracy": 0.0,
        "total_profit": 0.0,
        "avg_profit_per_round": 0.0,
        "best_round_accuracy": 0.0,
        "worst_round_accuracy": 0.0,
        "odds_coverage": 0.0,
    }


def compute_season_metrics(tips: Sequence[GradedTip]) -> SeasonStats:
    """Season stats for one heuristic, in the AFL comparison-dict shape.

    Profit settles every tip through :func:`settlement.settle_stake`
    ($10 stake; real price when present, $1.90 fallback otherwise;
    draws push) and ``odds_coverage`` counts tips settled at a usable
    real price — 0 for state leagues, which have no odds source.
    """
    if not tips:
        return _zero_stats()
    total_profit = 0.0
    total_correct = 0
    real_odds_tips = 0
    for tip in tips:
        total_profit += settle_stake(
            is_correct=tip.is_correct,
            is_draw=tip.is_draw,
            decimal_odds=tip.decimal_odds,
        )
        if tip.is_correct:
            total_correct += 1
        if is_valid_decimal_odds(tip.decimal_odds):
            real_odds_tips += 1
    accuracies = round_accuracy_values(tips)
    return {
        "total_rounds": len(accuracies),
        "total_tips": len(tips),
        "total_correct": total_correct,
        "overall_accuracy": total_correct / len(tips),
        "total_profit": total_profit,
        "avg_profit_per_round": (
            total_profit / len(accuracies) if accuracies else 0.0
        ),
        "best_round_accuracy": max(accuracies) if accuracies else 0.0,
        "worst_round_accuracy": min(accuracies) if accuracies else 0.0,
        "odds_coverage": real_odds_tips / len(tips),
    }


# ---------------------------------------------------------------------------
# Pure season selection — the D4 seasons payload, no database.
# ---------------------------------------------------------------------------


def resolve_current_label(seasons: Sequence[SeasonRef]) -> str | None:
    """The competition's current season label: ``is_current`` preferred,
    else the latest label — mirroring ``resolveCompetition()`` and
    ``select_tip_seasons`` (drift-safe against a stale flag)."""
    if not seasons:
        return None
    flagged = next((s for s in seasons if s.is_current), None)
    if flagged is not None:
        return flagged.label
    return max(season.label for season in seasons)


def build_seasons_payload(
    seasons: Sequence[SeasonRef],
    tipped_labels: Sequence[str],
) -> AvailableSeasons:
    """``{available_years, current_year}`` for a competition.

    ``available_years`` lists the seasons that HAVE league tips (the
    AFL ``get_available_seasons`` parity: only seasons with data are
    offered), latest label first.  ``current_year`` resolves across ALL
    the competition's seasons so the frontend's "most recent past
    season" logic works even before the current season has results.
    """
    return {
        "available_years": sorted(set(tipped_labels), reverse=True),
        "current_year": resolve_current_label(seasons),
    }


# ---------------------------------------------------------------------------
# Payload builders — pure mappers onto the AFL shapes.
# ---------------------------------------------------------------------------
def _no_best_overall() -> BestOverall:
    """The AFL ``/compare`` fallback for a season that does not exist
    (fresh dict — no shared mutable state)."""
    return {"heuristic": None, "accuracy": 0.0, "profit": 0.0}


def _zero_comparison() -> dict[str, SeasonStats]:
    """Every D3 heuristic at zero — the stable rendering contract."""
    return {heuristic: _zero_stats() for heuristic in LEAGUE_HEURISTICS}


def _best_overall(comparison: dict[str, SeasonStats]) -> BestOverall:
    """Top heuristic by accuracy (ties: canonical heuristic order) —
    the same ``max`` the AFL API endpoint applies to its comparison."""
    name, stats = max(
        comparison.items(), key=lambda item: item[1]["overall_accuracy"]
    )
    return {
        "heuristic": name,
        "accuracy": stats["overall_accuracy"],
        "profit": stats["total_profit"],
    }


def _zero_heuristic_entry(heuristic: str) -> HeuristicSeasonPerformance:
    return {
        "heuristic": heuristic,
        "total_profit": 0.0,
        "total_accuracy": 0.0,
        "rounds_played": 0,
        "avg_profit_per_round": 0.0,
        "projected_annual_profit": 0.0,
        "odds_coverage": 0.0,
    }


def _heuristic_current_entry(
    heuristic: str,
    stats: SeasonStats,
    total_rounds: int,
) -> HeuristicSeasonPerformance:
    """Map comparison stats onto the AFL current-season entry shape,
    including the linear-pace annual projection over fixture rounds."""
    rounds_played = stats["total_rounds"]
    avg_profit_per_round = (
        stats["total_profit"] / rounds_played if rounds_played > 0 else 0.0
    )
    return {
        "heuristic": heuristic,
        "total_profit": stats["total_profit"],
        "total_accuracy": stats["overall_accuracy"],
        "rounds_played": rounds_played,
        "avg_profit_per_round": avg_profit_per_round,
        "projected_annual_profit": avg_profit_per_round * total_rounds,
        "odds_coverage": stats["odds_coverage"],
    }


# ---------------------------------------------------------------------------
# DB layer — thin fetchers (patched out in unit tests) + the service.
# ---------------------------------------------------------------------------


class LeagueBacktestService:
    """Query-side league backtest for one competition (D4).

    All lookups are ``competition_id``-based; the API layer resolves the
    league key.  Three public mirrors of the AFL endpoints:

    * :meth:`get_available_seasons` — ``{available_years, current_year}``;
    * :meth:`compare_season`        — the ``/compare`` shape for an
      explicit season label (past-season comparisons);
    * :meth:`get_current_season_performance` — the current-season shape
      (label = ``is_current`` season, else the latest label).

    Empty states are well-defined zero structures the API layer can pass
    through untouched for the frontend's graceful rendering.
    """

    async def get_available_seasons(
        self, db: AsyncSession, *, competition_id: int
    ) -> AvailableSeasons:
        """Season labels with tips, latest first, plus the current label."""
        seasons = await self._fetch_seasons(db, competition_id)
        tipped_labels = await self._fetch_tipped_season_labels(db, competition_id)
        return build_seasons_payload(seasons, tipped_labels)

    async def compare_season(
        self, db: AsyncSession, *, competition_id: int, season_label: str
    ) -> SeasonComparison:
        """Per-heuristic comparison for an explicitly-named season.

        An unknown label degrades to the zero structure (label passed
        through) so a stale frontend link renders the empty state
        instead of erroring.
        """
        season_id = await self._fetch_season_id(db, competition_id, season_label)
        if season_id is None:
            return {
                "season": season_label,
                "comparison": _zero_comparison(),
                "best_overall": _no_best_overall(),
            }
        comparison = {
            heuristic: compute_season_metrics(
                await self._fetch_graded_tips(db, season_id, heuristic)
            )
            for heuristic in LEAGUE_HEURISTICS
        }
        return {
            "season": season_label,
            "comparison": comparison,
            "best_overall": _best_overall(comparison),
        }

    async def get_current_season_performance(
        self, db: AsyncSession, *, competition_id: int
    ) -> CurrentSeasonPerformance:
        """YTD performance for the current season, AFL shape.

        Rounds mirror the AFL BT-ROUND semantics: ``total_rounds`` from
        the fixture's distinct ``round_id`` values; ``rounds_completed``
        counts rounds where EVERY event is completed.
        """
        seasons = await self._fetch_seasons(db, competition_id)
        label = resolve_current_label(seasons)
        season_id = (
            await self._fetch_season_id(db, competition_id, label)
            if label is not None
            else None
        )
        if season_id is None:
            return {
                "season": label,
                "heuristics": [
                    _zero_heuristic_entry(heuristic)
                    for heuristic in LEAGUE_HEURISTICS
                ],
                "rounds_completed": 0,
                "total_rounds": 0,
            }
        total_rounds, rounds_completed = await self._fetch_fixture_rounds(
            db, season_id
        )
        heuristics = [
            _heuristic_current_entry(
                heuristic,
                compute_season_metrics(
                    await self._fetch_graded_tips(db, season_id, heuristic)
                ),
                total_rounds,
            )
            for heuristic in LEAGUE_HEURISTICS
        ]
        return {
            "season": label,
            "heuristics": heuristics,
            "rounds_completed": rounds_completed,
            "total_rounds": total_rounds,
        }

    # -- fetchers (patched out in unit tests) ---------------------------

    @staticmethod
    async def _fetch_seasons(
        db: AsyncSession, competition_id: int
    ) -> list[SeasonRef]:
        """Every season of the competition, any order (pure layer sorts)."""
        rows = (
            await db.execute(
                select(Season.id, Season.label, Season.is_current).where(
                    Season.competition_id == competition_id
                )
            )
        ).all()
        return [
            SeasonRef(id=int(row[0]), label=str(row[1]), is_current=bool(row[2]))
            for row in rows
        ]

    @staticmethod
    async def _fetch_tipped_season_labels(
        db: AsyncSession, competition_id: int
    ) -> list[str]:
        """Labels of the competition's seasons that HAVE league tips."""
        rows = (
            await db.execute(
                select(Season.label)
                .join(LeagueTip, LeagueTip.season_id == Season.id)
                .where(Season.competition_id == competition_id)
                .distinct()
                .order_by(Season.label.desc())
            )
        ).all()
        return [str(row[0]) for row in rows]

    @staticmethod
    async def _fetch_season_id(
        db: AsyncSession, competition_id: int, label: str
    ) -> int | None:
        """Resolve a season label within the competition (None if absent)."""
        return (
            await db.execute(
                select(Season.id).where(
                    Season.competition_id == competition_id,
                    Season.label == label,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _fetch_graded_tips(
        db: AsyncSession, season_id: int, heuristic: str
    ) -> list[GradedTip]:
        """Completed-event tips for one heuristic, graded at query time.

        The inner joins (both sides, both scores) mirror the AFL paths'
        ``completed AND scores present`` filter: only decided events are
        graded.  There is no multisport odds table yet, so every tip
        settles at ``FALLBACK_DECIMAL_ODDS`` — kept as an explicit
        ``decimal_odds=None`` argument so a future odds source plugs in
        without touching the pure layer.
        """
        home = aliased(EventParticipant)
        away = aliased(EventParticipant)
        rows = (
            await db.execute(
                select(
                    Event.round_id,
                    home.id,
                    away.id,
                    home.score,
                    away.score,
                    LeagueTip.selected_participant_id,
                )
                .join(Event, LeagueTip.event_id == Event.id)
                .join(home, and_(home.event_id == Event.id, home.side == "home"))
                .join(away, and_(away.event_id == Event.id, away.side == "away"))
                .where(
                    LeagueTip.season_id == season_id,
                    LeagueTip.heuristic == heuristic,
                    Event.completed.is_(True),
                    home.score.isnot(None),
                    away.score.isnot(None),
                )
            )
        ).all()
        return [
            grade_tip(
                round_id=row[0],
                home_side_id=int(row[1]),
                away_side_id=int(row[2]),
                home_score=row[3],
                away_score=row[4],
                selected_side_id=row[5],
                decimal_odds=None,
            )
            for row in rows
        ]

    @staticmethod
    async def _fetch_fixture_rounds(
        db: AsyncSession, season_id: int
    ) -> tuple[int, int]:
        """(total_rounds, rounds_completed) — the BT-ROUND semantics.

        ``total_rounds`` counts distinct fixture ``round_id`` values;
        ``rounds_completed`` counts rounds where ``bool_and(completed)``
        is true, i.e. every event in the round has been played.
        """
        total = (
            await db.execute(
                select(func.count(func.distinct(Event.round_id))).where(
                    Event.season_id == season_id,
                    Event.round_id.isnot(None),
                )
            )
        ).scalar()
        completed = (
            await db.execute(
                select(func.count()).select_from(
                    select(Event.round_id)
                    .where(
                        Event.season_id == season_id,
                        Event.round_id.isnot(None),
                    )
                    .group_by(Event.round_id)
                    .having(func.bool_and(Event.completed).is_(True))
                    .subquery()
                )
            )
        ).scalar()
        return int(total or 0), int(completed or 0)


__all__ = [
    "AvailableSeasons",
    "BestOverall",
    "CurrentSeasonPerformance",
    "GradedTip",
    "HeuristicSeasonPerformance",
    "LeagueBacktestService",
    "SeasonComparison",
    "SeasonStats",
    "build_seasons_payload",
    "compute_season_metrics",
    "grade_tip",
    "is_draw_score",
    "resolve_current_label",
    "round_accuracy_values",
    "winning_side_id",
]
