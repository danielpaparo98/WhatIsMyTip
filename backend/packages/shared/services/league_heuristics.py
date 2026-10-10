"""League heuristics — result-derived tips on the multisport model (D3).

The league-generic counterpart of the AFL heuristic engine: models
computable PURELY from ``Event``/``EventParticipant`` history — no
per-league code, no external features (weather, injuries, odds), so
any newly synced competition is backtestable immediately:

* ``home_advantage`` — always the home-side participant.
* ``form``           — the side with more wins in its last 5 completed
  events (chronological); tie → home.
* ``ladder``         — the side higher in the season-to-date standings
  (wins, then percentage); early-season tie → home.
* ``elo``            — the higher result-derived Elo rating (start
  1500, K=20, draws half); tie → home.
* ``matchup``        — the side with more head-to-head wins (any
  venue); tie → home.

Per-sport model registry (P2-3): a sport's model set is a
*registration* (:data:`SPORT_MODEL_SETS`), not a code branch.  AFL and
any unregistered sport keep the D3 trio (:data:`LEAGUE_HEURISTICS`);
rugby-league registers the reduced DB-only set (:data:`RUGBY_LEAGUE_MODELS`
— elo, form, home_advantage, matchup).  The AFL-scrape-sourced models
(weather_impact, injury_impact, player_form, value) are EXCLUDED for
rugby-league — they have no NRL source — and :func:`require_league_model`
rejects them with the repo-standard ``BackendServiceError``.

Structure (code-quality standard): the pick rules are pure functions
over frozen dataclasses — same input, same output, no DB — while
:class:`LeagueHeuristicsService` is a thin shell that loads the
competition's sport (which selects the model set), loads events,
delegates to the pure pipeline, and upserts onto ``league_tips``
idempotently against the UNIQUE ``(event_id, heuristic)`` constraint.
Correctness is never stored; grading happens at query time (subtask 04).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..logger import get_logger
from ..models import Competition, Event, EventParticipant, LeagueTip, Season
from ..sport_context import RUGBY_LEAGUE

logger = get_logger(__name__)

HEURISTIC_HOME_ADVANTAGE = "home_advantage"
HEURISTIC_FORM = "form"
HEURISTIC_LADDER = "ladder"
HEURISTIC_ELO = "elo"
HEURISTIC_MATCHUP = "matchup"

#: The D3 heuristic set, in canonical (persistence/grading) order.
LEAGUE_HEURISTICS: tuple[str, ...] = (
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_FORM,
    HEURISTIC_LADDER,
)

#: The reduced rugby-league model set (Phase 5.2, P2-3): exactly the
#: models computable from synced results alone — no AFL-calibrated ML
#: artifacts, no new training pipeline.  Registered for the three
#: national competitions (nrl / nrlw / origin, sport ``rugby-league``).
RUGBY_LEAGUE_MODELS: tuple[str, ...] = (
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_MATCHUP,
)

#: Per-sport league-model registry (P2-3), keyed on ``sport_id``.
#: ABSENT sports — AFL included — resolve to :data:`DEFAULT_MODEL_SET`,
#: so this map only ever carries *additional* sports.  Adding a sport
#: is a registration here, not an edit to the pipeline.
SPORT_MODEL_SETS: dict[str, tuple[str, ...]] = {
    RUGBY_LEAGUE.sport_id: RUGBY_LEAGUE_MODELS,
}

#: The default (bootstrap AFL / D3) set for any sport without an
#: explicit registration — the historical behaviour, byte-identical.
DEFAULT_MODEL_SET: tuple[str, ...] = LEAGUE_HEURISTICS

#: AFL-scrape-sourced models with NO rugby-league equivalent (Phase 5.2
#: scope decision, 2026-10-09): weather needs an NRL weather history,
#: injuries/player stats need NRL scrapers, value needs odds.  None has
#: a source yet, so requesting one for rugby-league is rejected cleanly
#: instead of silently producing garbage.
RUGBY_LEAGUE_EXCLUDED_MODELS: frozenset[str] = frozenset(
    {
        "weather_impact",
        "injury_impact",
        "player_form",
        "value",
    }
)

#: Form looks at each side's most recent completed events.
FORM_WINDOW = 5

#: Elo (result-derived, league pipeline): everyone starts level and a
#: decided result moves K/2 points net between the sides; a draw moves
#: nothing between even sides.
ELO_START_RATING = 1500.0
ELO_K_FACTOR = 20.0

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


def compute_elo_ratings(results: Sequence[CompletedEvent]) -> dict[int, float]:
    """Pure result-derived Elo ratings per participant.

    Everyone starts at :data:`ELO_START_RATING`; the walk applies the
    logistic update (400-point scale) in chronological order — a
    decided result shifts ``K/2`` points net between the sides, a draw
    between even sides shifts nothing.  Undecided rows (missing scores
    or sides) are skipped.  Sorting happens here so the output depends
    only on the SET of results, never on the caller's row order.
    """
    ratings: dict[int, float] = {}
    for event in sort_chronologically(results):
        home, away = event.home_participant_id, event.away_participant_id
        if home is None or away is None:
            continue
        home_score, away_score = event.home_score, event.away_score
        if home_score is None or away_score is None:
            continue
        r_home = ratings.get(home, ELO_START_RATING)
        r_away = ratings.get(away, ELO_START_RATING)
        expected_home = 1.0 / (1.0 + 10.0 ** ((r_away - r_home) / 400.0))
        if home_score > away_score:
            actual_home = 1.0
        elif home_score < away_score:
            actual_home = 0.0
        else:
            actual_home = 0.5
        ratings[home] = r_home + ELO_K_FACTOR * (actual_home - expected_home)
        ratings[away] = r_away + ELO_K_FACTOR * (
            (1.0 - actual_home) - (1.0 - expected_home)
        )
    return ratings


def pick_elo(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
) -> Optional[int]:
    """Side with the higher result-derived Elo rating; tie → home.

    ``prior_results`` must contain only events BEFORE this one (the
    no-look-ahead guarantee lives in the caller's chronological slice).
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    ratings = compute_elo_ratings(prior_results)
    if ratings.get(away, ELO_START_RATING) > ratings.get(home, ELO_START_RATING):
        return event.away_side_id
    return event.home_side_id


def head_to_head_wins(
    participant_a: int,
    participant_b: int,
    history: Sequence[CompletedEvent],
) -> tuple[int, int]:
    """Decided wins for ``(a, b)`` across meetings between the two.

    Venue-blind (home/away ignored) and draw-blind — only decided
    meetings between exactly these two participants count.
    """
    wins_a = 0
    wins_b = 0
    for event in history:
        home, away = event.home_participant_id, event.away_participant_id
        if home is None or away is None:
            continue
        if {home, away} != {participant_a, participant_b}:
            continue
        winner = _winner_participant_id(event)
        if winner == participant_a:
            wins_a += 1
        elif winner == participant_b:
            wins_b += 1
    return wins_a, wins_b


def pick_matchup(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
) -> Optional[int]:
    """Side with more head-to-head wins (any venue); tie → home.

    ``prior_results`` must contain only events BEFORE this one.  Draws
    between the pair never count as wins, so a pair that has only ever
    drawn resolves to the home-side default.
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    wins_home, wins_away = head_to_head_wins(home, away, prior_results)
    if wins_away > wins_home:
        return event.away_side_id
    return event.home_side_id


# ---------------------------------------------------------------------------
# Per-sport model registry (P2-3) — name → pure pick function.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueModelSpec:
    """One result-derived league model in the registry.

    ``pick`` receives the target event plus ONE history slice — the
    full chronological prefix when ``spans_seasons`` (form/elo/matchup:
    a team's recent results may reach into last season) or the
    same-season prefix otherwise (ladder: standings never cross
    seasons).
    """

    name: str
    pick: Callable[[CompletedEvent, Sequence[CompletedEvent]], Optional[int]]
    spans_seasons: bool


LEAGUE_MODELS: dict[str, LeagueModelSpec] = {
    spec.name: spec
    for spec in (
        LeagueModelSpec(
            HEURISTIC_HOME_ADVANTAGE,
            lambda event, _history: pick_home_advantage(event),
            spans_seasons=False,
        ),
        LeagueModelSpec(HEURISTIC_FORM, pick_form, spans_seasons=True),
        LeagueModelSpec(HEURISTIC_LADDER, pick_ladder, spans_seasons=False),
        LeagueModelSpec(HEURISTIC_ELO, pick_elo, spans_seasons=True),
        LeagueModelSpec(HEURISTIC_MATCHUP, pick_matchup, spans_seasons=True),
    )
}


def _validate_registry() -> None:
    """Fail fast on a registration referencing an unregistered pick."""
    for sport_id, names in SPORT_MODEL_SETS.items():
        unknown = [name for name in names if name not in LEAGUE_MODELS]
        if unknown:
            raise ValueError(
                f"Sport {sport_id!r} registers unknown league models: {unknown}"
            )


_validate_registry()


def league_model_set_for_sport(sport_id: Optional[str]) -> tuple[str, ...]:
    """The league-model set for ``sport_id``.

    Registered sports get their subset; AFL and every unregistered
    sport get :data:`DEFAULT_MODEL_SET` — the historical D3 behaviour,
    byte-identical for every existing competition.
    """
    if sport_id is None:
        return DEFAULT_MODEL_SET
    return SPORT_MODEL_SETS.get(sport_id, DEFAULT_MODEL_SET)


def _raise_model_unavailable(model: str, sport_id: Optional[str]) -> None:
    """Raise ``BackendServiceError`` with the repo-standard shape.

    The app-layer import is deferred: ``packages.shared`` stays free of
    ``app.*`` imports at module load time (the app imports shared,
    never the reverse) — the same pattern as ``national_leagues``.
    """
    from app.core.exceptions import BackendServiceError

    available = list(league_model_set_for_sport(sport_id))
    raise BackendServiceError(
        status_code=400,
        code="model_unavailable_for_sport",
        message=(
            f"Model '{model}' is not available for "
            f"{sport_id or 'this sport'} — it requires AFL data sources "
            f"with no NRL equivalent. Available models: "
            f"{', '.join(available)}"
        ),
        details={
            "model": model,
            "sport_id": sport_id,
            "available": available,
        },
    )


def require_league_model(model: str, *, sport_id: Optional[str]) -> tuple[str, ...]:
    """Resolve ``model`` against ``sport_id``'s league-model set.

    Returns the sport's set when the model is offered.  Raises
    ``ValueError`` for names unknown to the result-derived registry
    entirely, and the repo-standard ``BackendServiceError`` (400
    ``model_unavailable_for_sport``) for known-but-excluded models —
    pinned for the four AFL-scrape models under rugby-league.
    """
    if sport_id == RUGBY_LEAGUE.sport_id and model in RUGBY_LEAGUE_EXCLUDED_MODELS:
        _raise_model_unavailable(model, sport_id)
    if model not in LEAGUE_MODELS:
        raise ValueError(
            f"Unknown league model {model!r} — result-derived registry: "
            f"{sorted(LEAGUE_MODELS)}"
        )
    models = league_model_set_for_sport(sport_id)
    if model not in models:
        _raise_model_unavailable(model, sport_id)
    return models


def compute_event_picks(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    *,
    models: Sequence[str] = LEAGUE_HEURISTICS,
) -> list[TipPick]:
    """The registered models' tips for one completed event.

    ``models`` selects the per-sport set (P2-3) — the D3 default keeps
    the historical behaviour byte-identical.  ``prior_history`` is the
    cross-season chronological prefix (spanning models: form, elo,
    matchup); ``season_history`` the same-season prefix (ladder).  A
    drawn event persists draw-no-pick tips (NULL selection) for every
    model in the set; an event without both sides yields no tips at
    all, and a model that cannot resolve a side yields no tip of its
    own.
    """
    if event.home_side_id is None or event.away_side_id is None:
        return []
    if is_drawn(event):
        return [TipPick(event.event_id, name, None) for name in models]
    picks: list[TipPick] = []
    for name in models:
        spec = LEAGUE_MODELS[name]
        history = prior_history if spec.spans_seasons else season_history
        side = spec.pick(event, history)
        if side is not None:
            picks.append(TipPick(event.event_id, name, side))
    return picks


def compute_all_picks(
    events: Sequence[CompletedEvent],
    *,
    models: Sequence[str] = LEAGUE_HEURISTICS,
) -> list[TipPick]:
    """Tips for every completed event across the seasons being tipped.

    ``events`` spans the full completed-event history of the selected
    seasons.  Each event's evidence is strictly PRIOR: spanning models
    (form, elo, matchup) read the chronological prefix across seasons;
    season-scoped models (ladder) read the prefix scoped to the event's
    own season — no look-ahead, no cross-season standings.  ``models``
    selects the per-sport set; the default is the D3 trio.
    """
    history = sort_chronologically(events)
    picks: list[TipPick] = []
    for index, event in enumerate(history):
        prior = history[:index]
        same_season = [e for e in prior if e.season_id == event.season_id]
        picks.extend(compute_event_picks(event, prior, same_season, models=models))
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
        """Compute and persist the competition's tips; returns a summary.

        The competition's ``sport_id`` selects the per-sport model set
        (P2-3): rugby-league competitions (nrl / nrlw / origin) get the
        reduced DB-only set; AFL and unresolvable competitions keep the
        D3 trio — the historical behaviour, byte-identical.
        """
        sport_id = await self._fetch_sport_id(db, competition_id)
        models = league_model_set_for_sport(sport_id)
        seasons = await self._fetch_seasons(db, competition_id)
        selected = select_tip_seasons(seasons)
        if not selected:
            logger.info(
                "league heuristics: competition %s has no seasons — skipped",
                competition_id,
            )
            return _summary(competition_id, [], 0, 0, 0)

        events = await self._fetch_completed_events(db, [s.id for s in selected])
        picks = compute_all_picks(events, models=models)
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
    async def _fetch_sport_id(
        db: AsyncSession, competition_id: int
    ) -> Optional[str]:
        """The competition's sport id (``None`` when there is no row).

        The registry lookup defaults unknown/missing sports to the D3
        set, so an unresolvable competition keeps the historical
        behaviour rather than failing the pass.
        """
        sport = (
            await db.execute(
                select(Competition.sport_id).where(
                    Competition.id == competition_id
                )
            )
        ).scalar_one_or_none()
        return None if sport is None else str(sport)

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
