"""League heuristics — result-derived tips on the multisport model (D3).

The league-generic counterpart of the AFL heuristic engine: three
heuristics computable PURELY from ``Event``/``EventParticipant``
history — no per-league code, no external features (weather, injuries,
odds), so any newly synced competition is backtestable immediately:

* ``home_advantage`` — always the home-side participant.
* ``form``           — the side with more wins in its last 5 completed
  events (chronological); tie → home.
* ``ladder``         — the side higher in the season-to-date standings
  (wins, then percentage); early-season tie → home.

Structure (code-quality standard): the pick rules are pure functions
over frozen dataclasses — same input, same output, no DB — while
:class:`LeagueHeuristicsService` is a thin shell that loads events,
delegates to the pure pipeline, and upserts onto ``league_tips``
idempotently against the UNIQUE ``(event_id, heuristic)`` constraint.
Correctness is never stored; grading happens at query time (subtask 04).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..logger import get_logger
from ..models import Event, EventParticipant, LeagueTip, Season

logger = get_logger(__name__)

HEURISTIC_HOME_ADVANTAGE = "home_advantage"
HEURISTIC_FORM = "form"
HEURISTIC_LADDER = "ladder"

#: The D3 heuristic set, in canonical (persistence/grading) order.
LEAGUE_HEURISTICS: tuple[str, ...] = (
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_FORM,
    HEURISTIC_LADDER,
)

#: Form looks at each side's most recent completed events.
FORM_WINDOW = 5

_MIN_TIME = datetime.min


# ---------------------------------------------------------------------------
# Pure domain snapshots (no ORM, no DB — the pick functions' only inputs).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonRef:
    """The season identity the pure scope/pick functions need."""

    id: int
    label: str
    is_current: bool


@dataclass(frozen=True)
class CompletedEvent:
    """A completed event with both sides, in pick-function shape.

    ``*_side_id`` are ``event_participants.id`` values — the FK target
    of ``league_tips.selected_participant_id``; ``*_participant_id``
    are ``participants.id`` values — the identity that links a side to
    its history across events.  Scores are venue-local result scores;
    ``None`` scores mean an undecided row (treated as no result).
    """

    event_id: int
    season_id: int
    starts_at: Optional[datetime]
    home_side_id: Optional[int]
    away_side_id: Optional[int]
    home_participant_id: Optional[int]
    away_participant_id: Optional[int]
    home_score: Optional[int]
    away_score: Optional[int]


@dataclass(frozen=True)
class TipPick:
    """One heuristic's selection for one event (persisted as a tip row)."""

    event_id: int
    heuristic: str
    #: ``event_participants.id``; ``None`` = draw-no-pick.
    selected_side_id: Optional[int]


@dataclass(frozen=True)
class Standing:
    """One participant's season-to-date ladder record."""

    participant_id: int
    wins: int
    points_for: int
    points_against: int

    @property
    def percentage(self) -> float:
        """Points-for per 100 against (AFL ladder convention).

        Nothing conceded is infinitely good; nothing scored or played
        is zero.
        """
        if self.points_against == 0:
            return float("inf") if self.points_for else 0.0
        return self.points_for / self.points_against * 100.0


# ---------------------------------------------------------------------------
# Pure pick functions — the EXACT D3 semantics, fully unit-testable.
# ---------------------------------------------------------------------------


def sort_chronologically(events: Sequence[CompletedEvent]) -> list[CompletedEvent]:
    """Chronological order (venue-local kick-off, then event id).

    Rows without a kick-off sort first (oldest evidence).
    """
    return sorted(events, key=lambda e: (e.starts_at or _MIN_TIME, e.event_id))


def _winner_participant_id(event: CompletedEvent) -> Optional[int]:
    """Winning participant id, or ``None`` on a draw/undecided event."""
    if (
        event.home_score is None
        or event.away_score is None
        or event.home_participant_id is None
        or event.away_participant_id is None
    ):
        return None
    if event.home_score == event.away_score:
        return None
    if event.home_score > event.away_score:
        return event.home_participant_id
    return event.away_participant_id


def is_drawn(event: CompletedEvent) -> bool:
    """A completed event that ended level (both scores present, equal)."""
    return (
        event.home_score is not None
        and event.away_score is not None
        and event.home_score == event.away_score
    )


def pick_home_advantage(event: CompletedEvent) -> Optional[int]:
    """EXACT semantics: the home-side participant — always."""
    return event.home_side_id


def wins_in_last_n(
    participant_id: int,
    history: Sequence[CompletedEvent],
    n: int = FORM_WINDOW,
) -> int:
    """Wins across the participant's most recent *n* completed events.

    ``history`` must already be chronological (the caller owns the
    ordering); events without the participant are ignored and a draw
    is not a win.
    """
    if n <= 0:
        return 0
    involved = [
        event
        for event in history
        if participant_id in (event.home_participant_id, event.away_participant_id)
    ]
    return sum(
        1
        for event in involved[-n:]
        if _winner_participant_id(event) == participant_id
    )


def pick_form(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
) -> Optional[int]:
    """Side with more wins in its last 5 completed events; tie → home.

    ``prior_results`` must contain only events BEFORE this one (the
    no-look-ahead guarantee lives in the caller's chronological slice).
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    if wins_in_last_n(away, prior_results) > wins_in_last_n(home, prior_results):
        return event.away_side_id
    return event.home_side_id


def _ladder_key(row: Standing) -> tuple[int, float]:
    """Ladder sort key: wins first, then percentage."""
    return (row.wins, row.percentage)


def compute_standings(results: Sequence[CompletedEvent]) -> dict[int, Standing]:
    """Season-to-date ladder: wins and points per participant.

    Undecided rows (missing scores) and events without both sides are
    skipped; a draw contributes points but no wins.
    """
    table: dict[int, Standing] = {}
    for event in results:
        home, away = event.home_participant_id, event.away_participant_id
        if home is None or away is None:
            continue
        home_score, away_score = event.home_score, event.away_score
        if home_score is None or away_score is None:
            continue
        home_row = table.get(home, Standing(home, 0, 0, 0))
        away_row = table.get(away, Standing(away, 0, 0, 0))
        winner = _winner_participant_id(event)
        table[home] = Standing(
            home,
            home_row.wins + (1 if winner == home else 0),
            home_row.points_for + home_score,
            home_row.points_against + away_score,
        )
        table[away] = Standing(
            away,
            away_row.wins + (1 if winner == away else 0),
            away_row.points_for + away_score,
            away_row.points_against + home_score,
        )
    return table


def pick_ladder(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
) -> Optional[int]:
    """Side higher in the standings (wins, then %); tie → home.

    ``prior_results`` is the same-season chronological prefix — the
    season-to-date ladder as it stood coming into this event (an
    unplayed side ranks as 0 wins, 0%).
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    table = compute_standings(prior_results)
    home_row = table.get(home, Standing(home, 0, 0, 0))
    away_row = table.get(away, Standing(away, 0, 0, 0))
    if _ladder_key(away_row) > _ladder_key(home_row):
        return event.away_side_id
    return event.home_side_id


def compute_event_picks(
    event: CompletedEvent,
    form_history: Sequence[CompletedEvent],
    ladder_history: Sequence[CompletedEvent],
) -> list[TipPick]:
    """The three heuristic tips for one completed event.

    A drawn event persists draw-no-pick tips (NULL selection) for every
    heuristic; an event without both sides yields no tips at all, and
    a heuristic that cannot resolve a side yields no tip of its own.
    """
    if event.home_side_id is None or event.away_side_id is None:
        return []
    if is_drawn(event):
        return [TipPick(event.event_id, name, None) for name in LEAGUE_HEURISTICS]
    candidates: tuple[tuple[str, Optional[int]], ...] = (
        (HEURISTIC_HOME_ADVANTAGE, pick_home_advantage(event)),
        (HEURISTIC_FORM, pick_form(event, form_history)),
        (HEURISTIC_LADDER, pick_ladder(event, ladder_history)),
    )
    return [
        TipPick(event.event_id, name, side) for name, side in candidates if side is not None
    ]


def compute_all_picks(events: Sequence[CompletedEvent]) -> list[TipPick]:
    """Tips for every completed event across the seasons being tipped.

    ``events`` spans the full completed-event history of the selected
    seasons.  Each event's evidence is strictly PRIOR: form reads the
    chronological prefix across seasons (a team's last 5 may reach
    back into last season); ladder reads the prefix scoped to the
    event's own season — no look-ahead, no cross-season standings.
    """
    history = sort_chronologically(events)
    picks: list[TipPick] = []
    for index, event in enumerate(history):
        prior = history[:index]
        same_season = [e for e in prior if e.season_id == event.season_id]
        picks.extend(compute_event_picks(event, prior, same_season))
    return picks


def select_tip_seasons(seasons: Sequence[SeasonRef]) -> list[SeasonRef]:
    """D3 scope: the current season + the most recent past season.

    The ``is_current`` flag wins over label sort for the current pick
    (drift-safe: a stale flag on an older label still wins); without a
    flag the latest label is current.  The past pick is the latest
    label strictly BELOW the current label.  Returns ``[current]`` or
    ``[current, past]``.
    """
    ordered = sorted(seasons, key=lambda s: s.label, reverse=True)
    if not ordered:
        return []
    current = next((s for s in ordered if s.is_current), ordered[0])
    past = next((s for s in ordered if s.label < current.label), None)
    return [current, past] if past is not None else [current]


# ---------------------------------------------------------------------------
# DB layer — thin fetchers + idempotent upsert (the only impure code).
# ---------------------------------------------------------------------------


def _summary(
    competition_id: int,
    season_labels: list[str],
    events: int,
    inserted: int,
    updated: int,
) -> dict[str, Any]:
    """The generation result dict shared by every return path."""
    return {
        "competition_id": competition_id,
        "seasons": season_labels,
        "events_considered": events,
        "tips_inserted": inserted,
        "tips_updated": updated,
    }


class LeagueHeuristicsService:
    """Generate/refresh ``league_tips`` for one competition (D3 hook).

    Scope per D3: the CURRENT season (``is_current`` preferred, else
    latest label) plus the most recent past season, backfilling ALL
    completed events of both so the past-season backtest has data
    immediately.  Idempotent: re-running inserts nothing for unchanged
    picks, updates rows only where a recomputed pick differs (score
    revisions), and never trips the UNIQUE ``(event_id, heuristic)``
    constraint.
    """

    async def generate_for_competition(
        self, db: AsyncSession, *, competition_id: int
    ) -> dict[str, Any]:
        """Compute and persist the competition's tips; returns a summary."""
        seasons = await self._fetch_seasons(db, competition_id)
        selected = select_tip_seasons(seasons)
        if not selected:
            logger.info(
                "league heuristics: competition %s has no seasons — skipped",
                competition_id,
            )
            return _summary(competition_id, [], 0, 0, 0)

        events = await self._fetch_completed_events(db, [s.id for s in selected])
        picks = compute_all_picks(events)
        inserted, updated = await self._upsert_tips(
            db,
            picks,
            competition_id=competition_id,
            season_id_by_event={e.event_id: e.season_id for e in events},
        )
        await db.commit()
        summary = _summary(
            competition_id, [s.label for s in selected], len(events), inserted, updated
        )
        logger.info(
            "league heuristics: competition %s: %s", competition_id, summary
        )
        return summary

    # -- fetchers (patched out in unit tests) ---------------------------

    @staticmethod
    async def _fetch_seasons(
        db: AsyncSession, competition_id: int
    ) -> list[SeasonRef]:
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
    async def _fetch_completed_events(
        db: AsyncSession, season_ids: Sequence[int]
    ) -> list[CompletedEvent]:
        """Completed events with both sides, joined from event_participants.

        The inner joins drop n-side/missing-side rows — those can never
        carry a tip anyway (the pick FK targets the side row).
        """
        home = aliased(EventParticipant)
        away = aliased(EventParticipant)
        rows = (
            await db.execute(
                select(
                    Event.id,
                    Event.season_id,
                    Event.starts_at,
                    home.id,
                    away.id,
                    home.participant_id,
                    away.participant_id,
                    home.score,
                    away.score,
                )
                .join(home, and_(home.event_id == Event.id, home.side == "home"))
                .join(away, and_(away.event_id == Event.id, away.side == "away"))
                .where(
                    Event.season_id.in_([int(sid) for sid in season_ids]),
                    Event.completed.is_(True),
                )
            )
        ).all()
        return [
            CompletedEvent(
                event_id=int(row[0]),
                season_id=int(row[1]),
                starts_at=row[2],
                home_side_id=int(row[3]),
                away_side_id=int(row[4]),
                home_participant_id=int(row[5]),
                away_participant_id=int(row[6]),
                home_score=row[7],
                away_score=row[8],
            )
            for row in rows
        ]

    @staticmethod
    async def _fetch_existing_tips(
        db: AsyncSession, event_ids: Sequence[int]
    ) -> dict[tuple[int, str], Optional[int]]:
        """Persisted picks keyed (event_id, heuristic) for diffing."""
        ids = [int(eid) for eid in event_ids]
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(
                    LeagueTip.event_id,
                    LeagueTip.heuristic,
                    LeagueTip.selected_participant_id,
                ).where(LeagueTip.event_id.in_(ids))
            )
        ).all()
        return {(int(row[0]), str(row[1])): row[2] for row in rows}

    # -- persistence ------------------------------------------------------

    async def _upsert_tips(
        self,
        db: AsyncSession,
        picks: Sequence[TipPick],
        *,
        competition_id: int,
        season_id_by_event: dict[int, int],
    ) -> tuple[int, int]:
        """Diff picks against stored tips; insert new, update changed.

        Unchanged picks are untouched (no churn on re-sync).  The
        UNIQUE ``(event_id, heuristic)`` constraint is honoured by
        construction — one pick per key, new keys only inserted.
        """
        existing = await self._fetch_existing_tips(db, [p.event_id for p in picks])
        inserted = 0
        updated = 0
        for pick in picks:
            key = (pick.event_id, pick.heuristic)
            if key not in existing:
                db.add(
                    LeagueTip(
                        event_id=pick.event_id,
                        heuristic=pick.heuristic,
                        selected_participant_id=pick.selected_side_id,
                        competition_id=competition_id,
                        season_id=season_id_by_event[pick.event_id],
                    )
                )
                inserted += 1
            elif existing[key] != pick.selected_side_id:
                if await self._update_tip(
                    db, pick.event_id, pick.heuristic, pick.selected_side_id
                ):
                    updated += 1
        await db.flush()
        return inserted, updated

    @staticmethod
    async def _update_tip(
        db: AsyncSession,
        event_id: int,
        heuristic: str,
        selected_side_id: Optional[int],
    ) -> bool:
        """Point one stored tip at its recomputed selection (score revision)."""
        result = await db.execute(
            select(LeagueTip).where(
                LeagueTip.event_id == event_id,
                LeagueTip.heuristic == heuristic,
            )
        )
        tip = result.scalar_one_or_none()
        if tip is None:
            return False
        tip.selected_participant_id = selected_side_id
        return True


async def generate_league_tips(
    db: AsyncSession, *, competition_id: int
) -> dict[str, Any]:
    """Module-level entry point — the local_competition_sync hook target."""
    return await LeagueHeuristicsService().generate_for_competition(
        db, competition_id=competition_id
    )
