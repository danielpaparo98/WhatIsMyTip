"""League heuristics â€” result-derived tips on the multisport model (D3).

The league-generic counterpart of the AFL heuristic engine: models
computable PURELY from ``Event``/``EventParticipant`` history â€” no
per-league code, no external features (weather, injuries, odds), so
any newly synced competition is backtestable immediately:

* ``home_advantage`` â€” always the home-side participant.
* ``form``           â€” the side with more wins in its last 5 completed
  events (chronological); tie â†’ home.
* ``ladder``         â€” the side higher in the season-to-date standings
  (wins, then percentage); early-season tie â†’ home.
* ``elo``            â€” the higher result-derived Elo rating (start
  1500, K=20, draws half); tie â†’ home.
* ``matchup``        â€” the side with more head-to-head wins (any
  venue); tie â†’ home.

Per-sport model registry (P2-3): a sport's model set is a
*registration* (:data:`SPORT_MODEL_SETS`), not a code branch.  AFL and
any unregistered sport keep the D3 trio (:data:`LEAGUE_HEURISTICS`);
rugby-league registers the reduced DB-only set (:data:`RUGBY_LEAGUE_MODELS`
â€” elo, form, home_advantage, matchup).  The AFL-scrape-sourced models
(weather_impact, injury_impact, player_form, value) are EXCLUDED for
rugby-league â€” they have no NRL source â€” and :func:`require_league_model`
rejects them with the repo-standard ``BackendServiceError``.

Per-league model registry (FCL-2): a competition's model set is, the
same way, a *registration* (:data:`LEAGUE_MODEL_SETS`) layered above
the per-sport default.  Resolution is league â†’ sport â†’ default
(:func:`league_model_set_for_competition`), keyed on the stable
competition name (``LeagueConfig.name`` == ``competitions.name``): a
registered league gets exactly its registered set; every unregistered
competition â€” AFL included â€” resolves through its sport, byte-identical
to the pre-FCL behaviour.  Values hold BASE pick models only: the
derived heuristics (best_bet / weighted_tip / boosted_tip) are built
ON the resolved set by the generation pipeline and are never registry
entries.

Generation wiring (FCL-6): :meth:`LeagueHeuristicsService.
generate_for_competition` resolves the competition's set through the
FULL registry (league â†’ sport â†’ default, keyed on the competition NAME
as well as its sport) and persists the base models' tips PLUS the
three derived heuristics every league gets
(:data:`DERIVED_LEAGUE_HEURISTICS`): ``best_bet`` â€” a pure majority
vote over the base picks, available instantly on sync with no training
dependency â€” and the ``weighted_tip`` / ``boosted_tip`` combiners,
which load the competition's scoped trained weights/artifact (FCL-4
CRUD) and fall back to the same majority vote before a league's first
training run or on ANY load/prediction failure â€” tip generation never
crashes on a model.  Both learned paths read the exact chronological
prefix slices the base picks read (:func:`compute_all_picks_and_derived`
walks history once, so no derived pick can see its own event's
result).  Derived heuristic names stay global on ``league_tips`` (each
event belongs to exactly one competition, so the UNIQUE
``(event_id, heuristic)`` constraint is safe) while every row carries
the competition id.

Structure (code-quality standard): the pick rules are pure functions
over frozen dataclasses â€” same input, same output, no DB â€” while
:class:`LeagueHeuristicsService` is a thin shell that loads the
competition's sport (which selects the model set), loads events,
delegates to the pure pipeline, and upserts onto ``league_tips``
idempotently against the UNIQUE ``(event_id, heuristic)`` constraint.
Correctness is never stored; grading happens at query time (subtask 04).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..logger import get_logger
from ..models import Competition, Event, EventParticipant, LeagueTip, Participant, Season
from ..sport_context import RUGBY_LEAGUE

logger = get_logger(__name__)

HEURISTIC_HOME_ADVANTAGE = "home_advantage"
HEURISTIC_FORM = "form"
HEURISTIC_LADDER = "ladder"
HEURISTIC_ELO = "elo"
HEURISTIC_MATCHUP = "matchup"
#: PARITY (2026-10-14): the AFL ``value`` model's result-derived league
#: counterpart â€” all-time win-rate differential â€” is computable from
#: synced results alone, so every league now gets it as a base pick.
HEURISTIC_VALUE = "value"

#: The D3 heuristic set, in canonical (persistence/grading) order.
LEAGUE_HEURISTICS: tuple[str, ...] = (
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_FORM,
    HEURISTIC_LADDER,
)

#: PARITY (2026-10-14, absolute AFL/league parity): ONE canonical base
#: set for EVERY league â€” the six models the AFL flagship runs, minus
#: the two with no non-AFL data source (``injury_impact`` /
#: ``player_form``).  ``weather_impact`` â€” the sixth parity model â€” is
#: computed on the numeric predictions pass
#: (:mod:`.league_model_predictions`) rather than as a pure pick,
#: because it reads the weather snapshots, not results.  Registered for
#: rugby-league explicitly and used as the default for every other
#: sport, so state leagues gain elo/matchup/value and the NRL family
#: gains value with zero per-league code.
PARITY_LEAGUE_MODEL_SET: tuple[str, ...] = (
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_MATCHUP,
    HEURISTIC_VALUE,
)

#: The reduced rugby-league model set (Phase 5.2, P2-3) â€” now the
#: parity set (see above).
RUGBY_LEAGUE_MODELS: tuple[str, ...] = PARITY_LEAGUE_MODEL_SET

#: Per-sport league-model registry (P2-3), keyed on ``sport_id``.
#: ABSENT sports â€” AFL included â€” resolve to :data:`DEFAULT_MODEL_SET`,
#: so this map only ever carries *additional* sports.  Adding a sport
#: is a registration here, not an edit to the pipeline.
SPORT_MODEL_SETS: dict[str, tuple[str, ...]] = {
    RUGBY_LEAGUE.sport_id: RUGBY_LEAGUE_MODELS,
}

#: The default set for any sport without an explicit registration â€”
#: the parity set (PARITY, 2026-10-14): every competition the site
#: serves gets the same five result-derived base models.
DEFAULT_MODEL_SET: tuple[str, ...] = PARITY_LEAGUE_MODEL_SET

#: The three DERIVED heuristics (FCL-6) every competition gets, built ON
#: the resolved base set by the generation pipeline â€” never registry
#: entries themselves (pinned by ``TestLeagueModelSets``).  The strings
#: are the trio's ``league_tips``-global identity shared with the AFL
#: path; ``best_bet``'s value is kept in lockstep with
#: ``league_consensus.HEURISTIC_BEST_BET`` â€” that module imports THIS
#: one, so the constant lives here and the mirror is pinned by tests.
HEURISTIC_BEST_BET = "best_bet"
HEURISTIC_WEIGHTED_TIP = "weighted_tip"
HEURISTIC_BOOSTED_TIP = "boosted_tip"
#: FCL-14 (AFL parity): the AFL orchestrator's third generated heuristic
#: â€” the single-strongest-model pick â€” now exists for every league too,
#: so the selectable heuristic set is IDENTICAL across AFL and leagues.
HEURISTIC_YOLO = "yolo"

#: Derived-heuristic persistence/summary order (FCL-6).
DERIVED_LEAGUE_HEURISTICS: tuple[str, ...] = (
    HEURISTIC_BEST_BET,
    HEURISTIC_WEIGHTED_TIP,
    HEURISTIC_BOOSTED_TIP,
    HEURISTIC_YOLO,
)

#: AFL-scrape-sourced models with NO league source (PARITY revision of
#: the Phase 5.2 scope decision): player injuries and player statistics
#: need scrapers that only exist for the AFL.  ``value`` left this list
#: (it is result-derived and now a base pick for every league) and
#: ``weather_impact`` left it too (Open-Meteo serves every venue) â€”
#: requesting either for rugby-league resolves through the registry
#: like any other model.  Requesting one of the remaining two raises
#: the repo-standard ``BackendServiceError`` 400
#: ``model_unavailable_for_sport``.
RUGBY_LEAGUE_EXCLUDED_MODELS: frozenset[str] = frozenset(
    {
        "injury_impact",
        "player_form",
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
# Pure domain snapshots (no ORM, no DB â€” the pick functions' only inputs).
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

    ``*_side_id`` are ``event_participants.id`` values â€” the FK target
    of ``league_tips.selected_participant_id``; ``*_participant_id``
    are ``participants.id`` values â€” the identity that links a side to
    its history across events.  Scores are venue-local result scores;
    ``None`` scores mean an undecided row (treated as no result).

    FCL-12: the snapshot also carries SCHEDULED fixtures (forward
    tips).  ``completed=False`` marks one: its scores are unset â€” or,
    for feeds that ship pre-match 0-0 placeholders, deliberately
    meaningless â€” and :func:`is_drawn` refuses to call it a draw.
    Defaults to ``True`` so every completed-event construction site
    (and every existing test factory) is unchanged.

    PARITY-1: the snapshot also carries the VENUE â€” the weather-impact
    model's same-venue history filter reads it.  ``None`` (the default)
    keeps every existing construction site unchanged.

    PARITY-1b: the snapshot also carries the ROUND â€” the grand-final
    report's finals path labels results by round.  ``None`` (the
    default) keeps every existing construction site unchanged.
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
    completed: bool = True
    venue: Optional[str] = None
    round_id: Optional[int] = None


@dataclass(frozen=True)
class TipPick:
    """One heuristic's selection for one event (persisted as a tip row).

    FCL-14: the derived heuristics also carry the AFL tip shape â€”
    ``confidence`` (0..1) and home-signed ``margin`` (points) â€” so the
    league cards render exactly like the AFL cards.  ``None`` for the
    base result-derived models (picks, not numbers) and draw-no-picks.
    """

    event_id: int
    heuristic: str
    #: ``event_participants.id``; ``None`` = draw-no-pick.
    selected_side_id: Optional[int]
    confidence: Optional[float] = None
    margin: Optional[int] = None


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
# Pure pick functions â€” the EXACT D3 semantics, fully unit-testable.
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
    """A COMPLETED event that ended level (both scores present, equal).

    FCL-12: an un-completed snapshot is NEVER a draw â€” feeds ship
    pre-match 0-0 score placeholders on scheduled fixtures, and without
    the completion guard every forward tip would degenerate to
    draw-no-pick NULLs (found live on AFLW round 10).
    """
    return (
        event.completed
        and event.home_score is not None
        and event.away_score is not None
        and event.home_score == event.away_score
    )


def pick_home_advantage(event: CompletedEvent) -> Optional[int]:
    """EXACT semantics: the home-side participant â€” always."""
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
    """Side with more wins in its last 5 completed events; tie â†’ home.

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
    """Side higher in the standings (wins, then %); tie â†’ home.

    ``prior_results`` is the same-season chronological prefix â€” the
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
    logistic update (400-point scale) in chronological order â€” a
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
    """Side with the higher result-derived Elo rating; tie â†’ home.

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

    Venue-blind (home/away ignored) and draw-blind â€” only decided
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
    """Side with more head-to-head wins (any venue); tie â†’ home.

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


def compute_win_rates(results: Sequence[CompletedEvent]) -> dict[int, float]:
    """All-time decided win rate per participant (0..1).

    The league counterpart of the AFL ``value`` model's aggregated
    win-rate query: draws are excluded from the denominator (neither
    side won), undecided rows are skipped entirely.  Sorting does not
    matter â€” the output depends only on the SET of results.
    """
    wins: dict[int, int] = {}
    decided: dict[int, int] = {}
    for event in results:
        home, away = event.home_participant_id, event.away_participant_id
        if home is None or away is None:
            continue
        home_score, away_score = event.home_score, event.away_score
        if home_score is None or away_score is None:
            continue
        if home_score == away_score:
            continue  # a draw is a win for neither side
        winner = home if home_score > away_score else away
        loser = away if winner == home else home
        wins[winner] = wins.get(winner, 0) + 1
        decided[winner] = decided.get(winner, 0) + 1
        decided[loser] = decided.get(loser, 0) + 1
    return {
        participant: (wins.get(participant, 0) / games)
        for participant, games in decided.items()
    }


def pick_value(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
) -> Optional[int]:
    """Side with the better all-time win rate; tie â†’ home.

    The league counterpart of the AFL ``value`` model's pick rule
    (raw rates only â€” home advantage is owned by the
    ``home_advantage`` model).  ``prior_results`` must contain only
    events BEFORE this one.
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    rates = compute_win_rates(prior_results)
    if rates.get(away, 0.5) > rates.get(home, 0.5):
        return event.away_side_id
    return event.home_side_id


# ---------------------------------------------------------------------------
# Per-sport model registry (P2-3) â€” name â†’ pure pick function.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueModelSpec:
    """One result-derived league model in the registry.

    ``pick`` receives the target event plus ONE history slice â€” the
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
        LeagueModelSpec(HEURISTIC_VALUE, pick_value, spans_seasons=True),
    )
}


#: Per-league model-set registry (FCL-2), keyed on the competition
#: name (``LeagueConfig.name`` == ``competitions.name`` â€” the stable
#: identity that survives re-seeding).  Values are tuples of BASE pick
#: models â€” every entry MUST exist in :data:`LEAGUE_MODELS`; the
#: derived heuristics (best_bet / weighted_tip / boosted_tip) are
#: computed FROM the resolved set by the generation pipeline and are
#: never registered here.  Like :data:`SPORT_MODEL_SETS`, this map
#: only ever carries leagues whose set DIFFERS from their sport's
#: default, so an empty map is the correct bootstrap: every competition
#: resolves through its sport, byte-identical.  Adding a league
#: override is a registration here, not an edit to the pipeline; the
#: import-time validation below fails fast on an unknown model name
#: or an empty set.
LEAGUE_MODEL_SETS: dict[str, tuple[str, ...]] = {}


def _validate_registry() -> None:
    """Fail fast on a registration referencing an unregistered pick."""
    for sport_id, names in SPORT_MODEL_SETS.items():
        unknown = [name for name in names if name not in LEAGUE_MODELS]
        if unknown:
            raise ValueError(
                f"Sport {sport_id!r} registers unknown league models: {unknown}"
            )
    for competition_name, names in LEAGUE_MODEL_SETS.items():
        if not names:
            raise ValueError(
                f"Competition {competition_name!r} registers an empty "
                f"league model set"
            )
        unknown = [name for name in names if name not in LEAGUE_MODELS]
        if unknown:
            raise ValueError(
                f"Competition {competition_name!r} registers unknown "
                f"league models: {unknown}"
            )


_validate_registry()


def league_model_set_for_sport(sport_id: Optional[str]) -> tuple[str, ...]:
    """The league-model set for ``sport_id``.

    Registered sports get their subset; AFL and every unregistered
    sport get :data:`DEFAULT_MODEL_SET` â€” the historical D3 behaviour,
    byte-identical for every existing competition.
    """
    if sport_id is None:
        return DEFAULT_MODEL_SET
    return SPORT_MODEL_SETS.get(sport_id, DEFAULT_MODEL_SET)


def league_model_set_for_competition(
    name: Optional[str],
    sport_id: Optional[str] = None,
) -> tuple[str, ...]:
    """The league-model set for one competition (FCL-2).

    Resolution is league â†’ sport â†’ default: a competition name
    registered in :data:`LEAGUE_MODEL_SETS` gets exactly that set;
    every other name falls through to :func:`league_model_set_for_sport`
    UNCHANGED â€” so an unregistered competition (AFL included) resolves
    byte-identically to the pre-FCL behaviour.  An unresolvable name
    (``None`` â€” a competition row not yet synced) falls through the
    same way rather than failing the pass.
    """
    registered = LEAGUE_MODEL_SETS.get(name)
    if registered is not None:
        return registered
    return league_model_set_for_sport(sport_id)


def _raise_model_unavailable(model: str, sport_id: Optional[str]) -> None:
    """Raise ``BackendServiceError`` with the repo-standard shape.

    The app-layer import is deferred: ``packages.shared`` stays free of
    ``app.*`` imports at module load time (the app imports shared,
    never the reverse) â€” the same pattern as ``national_leagues``.
    """
    from app.core.exceptions import BackendServiceError

    available = list(league_model_set_for_sport(sport_id))
    raise BackendServiceError(
        status_code=400,
        code="model_unavailable_for_sport",
        message=(
            f"Model '{model}' is not available for "
            f"{sport_id or 'this sport'} â€” it requires AFL data sources "
            f"(player injuries / player statistics) with no league "
            f"equivalent. Available models: {', '.join(available)}"
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
    ``model_unavailable_for_sport``) for known-but-excluded models â€”
    pinned for the four AFL-scrape models under rugby-league.
    """
    if sport_id == RUGBY_LEAGUE.sport_id and model in RUGBY_LEAGUE_EXCLUDED_MODELS:
        _raise_model_unavailable(model, sport_id)
    if model not in LEAGUE_MODELS:
        raise ValueError(
            f"Unknown league model {model!r} â€” result-derived registry: "
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

    ``models`` selects the per-sport set (P2-3) â€” the D3 default keeps
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
    own season â€” no look-ahead, no cross-season standings.  ``models``
    selects the per-sport set; the default is the D3 trio.
    """
    history = sort_chronologically(events)
    picks: list[TipPick] = []
    for index, event in enumerate(history):
        prior = history[:index]
        same_season = [e for e in prior if e.season_id == event.season_id]
        picks.extend(compute_event_picks(event, prior, same_season, models=models))
    return picks


# ---------------------------------------------------------------------------
# Derived heuristics (FCL-6) â€” best_bet consensus + learned combiners,
# built ON the base picks over the SAME chronological slices.
# ---------------------------------------------------------------------------


def _league_linear_pick(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    base_picks: Sequence[TipPick],
    model_names: Sequence[str],
    linear_weights: Optional[tuple[float, Mapping[str, float]]],
) -> TipPick:
    """The ``weighted_tip`` pick for one event (FCL-6).

    Learned linear combiner when the competition's scoped weights are
    loaded (FCL-4 CRUD â†’ FCL-3 composite), the majority vote otherwise
    (FCL-3d).  A learned prediction that RAISES â€” a stale feature
    contract, a corrupt row â€” demotes to the same fallback with a
    warning instead of failing the pass: generation never crashes on a
    model.  The import is deferred (``league_consensus`` imports this
    module, so a top-level edge would be circular).
    """
    from .league_consensus import league_consensus_fallback, league_linear_tip

    if linear_weights is None:
        tip = league_consensus_fallback(event, base_picks)
    else:
        intercept, coefficients = linear_weights
        try:
            tip = league_linear_tip(
                event,
                prior_history,
                season_history,
                intercept=intercept,
                coefficients=coefficients,
                model_names=model_names,
            )
        except Exception:  # noqa: BLE001 â€” a broken model never fails generation
            logger.warning(
                "league heuristics: learned weighted_tip prediction failed "
                "for event %s â€” using the majority-vote fallback",
                event.event_id,
                exc_info=True,
            )
            tip = league_consensus_fallback(event, base_picks)
    # FCL-14: the combiner's full AFL-shaped tip (side + confidence +
    # margin) is preserved â€” the league cards render the same numbers
    # the AFL cards do.  A draw-no-pick keeps both numbers NULL (the
    # fallback's advisory values are not a card's numbers).
    return TipPick(
        event.event_id,
        HEURISTIC_WEIGHTED_TIP,
        tip.selected_side_id,
        tip.confidence if tip.selected_side_id is not None else None,
        tip.margin if tip.selected_side_id is not None else None,
    )


def _league_boosted_pick(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    base_picks: Sequence[TipPick],
    model_names: Sequence[str],
    boosted_model: Optional[Any],
) -> TipPick:
    """The ``boosted_tip`` pick for one event (FCL-6).

    Same contract as :func:`_league_linear_pick` with the XGBoost
    combiner in place of the linear one â€” the model arrives already
    deserialized (BT-2: the service pays the artifact load once per
    pass, not per event).
    """
    from .league_consensus import league_boosted_tip, league_consensus_fallback

    if boosted_model is None:
        tip = league_consensus_fallback(event, base_picks)
    else:
        try:
            tip = league_boosted_tip(
                event,
                prior_history,
                season_history,
                boosted_model,
                model_names=model_names,
            )
        except Exception:  # noqa: BLE001 â€” a broken model never fails generation
            logger.warning(
                "league heuristics: learned boosted_tip prediction failed "
                "for event %s â€” using the majority-vote fallback",
                event.event_id,
                exc_info=True,
            )
            tip = league_consensus_fallback(event, base_picks)
    # FCL-14: the combiner's full AFL-shaped tip (side + confidence +
    # margin) is preserved â€” the league cards render the same numbers
    # the AFL cards do.  A draw-no-pick keeps both numbers NULL (the
    # fallback's advisory values are not a card's numbers).
    return TipPick(
        event.event_id,
        HEURISTIC_BOOSTED_TIP,
        tip.selected_side_id,
        tip.confidence if tip.selected_side_id is not None else None,
        tip.margin if tip.selected_side_id is not None else None,
    )


def compute_derived_picks(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    base_picks: Sequence[TipPick],
    *,
    model_names: Sequence[str] = LEAGUE_HEURISTICS,
    linear_weights: Optional[tuple[float, Mapping[str, float]]] = None,
    boosted_model: Optional[Any] = None,
) -> list[TipPick]:
    """The four derived heuristics' tips for one event (FCL-6, FCL-14).

    ``best_bet`` is always the pure majority vote over ``base_picks``
    (FCL-3a) â€” no training dependency, correct from the first sync.
    ``weighted_tip``/``boosted_tip`` take the learned combiner when the
    caller supplies the competition's scoped weights/model and fall
    back to the same majority vote otherwise (FCL-3d); a learned
    prediction failure demotes to the fallback rather than raising.
    ``yolo`` (FCL-14, AFL parity) is the single strongest base-model
    signal's pick (:func:`league_consensus.league_yolo_pick`).

    Every derived tip carries the AFL card shape â€” confidence (0..1)
    and home-signed margin (points) â€” so the league cards render
    exactly like the AFL cards (FCL-14).

    ``prior_history``/``season_history`` must be the EXACT prefix
    slices the base picks were computed from â€” the learned features
    then see precisely the evidence the base picks saw (no look-ahead;
    the caller owns the chronological slicing, as for every pick
    function).  ``model_names`` is the league's resolved BASE set: it
    orders both the feature contract and the vote.

    Returns up to four picks in :data:`DERIVED_LEAGUE_HEURISTICS`
    order.  A drawn event yields draw-no-pick (NULL) tips for all
    four, matching the base models' own draw discipline; an event
    without both sides yields no tips at all (the pick FK targets the
    side rows).
    """
    from .league_consensus import league_yolo_pick, pick_best_bet

    if event.home_side_id is None or event.away_side_id is None:
        return []
    return [
        pick_best_bet(event, base_picks),
        _league_linear_pick(
            event, prior_history, season_history, base_picks, model_names,
            linear_weights,
        ),
        _league_boosted_pick(
            event, prior_history, season_history, base_picks, model_names,
            boosted_model,
        ),
        league_yolo_pick(event, prior_history, season_history, model_names),
    ]


def compute_all_picks_and_derived(
    events: Sequence[CompletedEvent],
    *,
    models: Sequence[str] = LEAGUE_HEURISTICS,
    linear_weights: Optional[tuple[float, Mapping[str, float]]] = None,
    boosted_model: Optional[Any] = None,
    scheduled: Sequence[CompletedEvent] | None = None,
) -> list[TipPick]:
    """Base + derived tips for every event in ONE walk (FCL-6, FCL-12).

    The completed events walk exactly as before (positional
    chronological slices).  ``scheduled`` â€” FCL-12 forward tips â€” walks
    the upcoming fixtures afterwards, each reading an evidence slice of
    ONLY the completed events that start no later than the fixture (no
    look-ahead, and the fixture's own pre-match score placeholders
    never enter standings/Elo), then no-look-ahead for the derived
    heuristics from the same slices.  A scheduled snapshot is never a
    draw (:func:`is_drawn` requires completion), so its picks are real
    side selections.
    ``linear_weights``/``boosted_model`` are the competition's scoped
    learned combiners (``None`` â†’ majority-vote fallback, FCL-3d);
    ``models`` selects the per-sport/league BASE set exactly as for
    :func:`compute_all_picks`.
    """
    history = sort_chronologically(events)
    picks: list[TipPick] = []
    for index, event in enumerate(history):
        prior = history[:index]
        same_season = [e for e in prior if e.season_id == event.season_id]
        base = compute_event_picks(event, prior, same_season, models=models)
        picks.extend(base)
        picks.extend(
            compute_derived_picks(
                event,
                prior,
                same_season,
                base,
                model_names=models,
                linear_weights=linear_weights,
                boosted_model=boosted_model,
            )
        )

    scheduled_history = sort_chronologically(scheduled or [])
    for event in scheduled_history:
        if not event.completed:
            prior = [
                e
                for e in history
                if e.completed
                and (e.starts_at or _MIN_TIME) <= (event.starts_at or _MIN_TIME)
            ]
        else:  # defensive: a completed snapshot belongs in ``events``
            prior = history
        same_season = [e for e in prior if e.season_id == event.season_id]
        base = compute_event_picks(event, prior, same_season, models=models)
        picks.extend(base)
        picks.extend(
            compute_derived_picks(
                event,
                prior,
                same_season,
                base,
                model_names=models,
                linear_weights=linear_weights,
                boosted_model=boosted_model,
            )
        )
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
# DB layer â€” thin fetchers + idempotent upsert (the only impure code).
# ---------------------------------------------------------------------------


def _summary(
    competition_id: int,
    season_labels: list[str],
    events: int,
    inserted: int,
    updated: int,
    derived: Optional[Mapping[str, int]] = None,
    scheduled: int = 0,
) -> dict[str, Any]:
    """The generation result dict shared by every return path.

    FCL-6 adds ``derived_tips`` â€” per-derived-heuristic insert counts
    (best_bet / weighted_tip / boosted_tip).  Every competition gets
    all three derived heuristics, so the key is ALWAYS present (zeros
    when there was nothing to generate); the failure-tolerant sync
    caller merges the dict as-is, so existing keys are untouched.
    FCL-12 adds ``scheduled_considered`` â€” upcoming fixtures tipped
    this pass (forward tips; graded only once completed)."""
    return {
        "competition_id": competition_id,
        "seasons": season_labels,
        "events_considered": events,
        "scheduled_considered": scheduled,
        "tips_inserted": inserted,
        "tips_updated": updated,
        "derived_tips": {
            name: derived.get(name, 0) if derived else 0
            for name in DERIVED_LEAGUE_HEURISTICS
        },
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

        FCL-6: the model set resolves through the FULL registry â€”
        league (by competition NAME) â†’ sport â†’ default â€” and the pass
        persists the base models' tips PLUS the three derived
        heuristics every league gets: ``best_bet`` (pure majority vote
        over the base picks, correct from the first sync) and the
        ``weighted_tip``/``boosted_tip`` combiners, which load the
        competition's scoped active weights/artifact and fall back to
        the same majority vote before training or on any load failure â€”
        generation NEVER crashes on a model.  Derived names stay global
        on ``league_tips`` (each event belongs to exactly one league,
        so UNIQUE ``(event_id, heuristic)`` is safe) while every row
        carries this ``competition_id``.  An unresolvable competition
        (no row / no sport) keeps the D3 trio + derived names rather
        than failing the pass.
        """
        sport_id = await self._fetch_sport_id(db, competition_id)
        name = await self._fetch_competition_name(db, competition_id)
        models = league_model_set_for_competition(name, sport_id)
        seasons = await self._fetch_seasons(db, competition_id)
        selected = select_tip_seasons(seasons)
        if not selected:
            logger.info(
                "league heuristics: competition %s has no seasons â€” skipped",
                competition_id,
            )
            return _summary(competition_id, [], 0, 0, 0)

        events = await self._fetch_completed_events(db, [s.id for s in selected])
        # FCL-12 forward tips: scheduled fixtures with both sides join the
        # SAME chronological walk.  Every pick reads only prior completed
        # evidence, so an upcoming fixture gets a real tip now and the
        # idempotent upsert refreshes it as results land and retrains
        # promote new versions.  Grading is untouched (completed only).
        scheduled = await self._fetch_scheduled_events(
            db, [s.id for s in selected]
        )
        linear_weights = await self._load_linear_weights(
            db, competition_id=competition_id
        )
        boosted_model = await self._load_boosted_artifact(
            db, competition_id=competition_id
        )
        picks = compute_all_picks_and_derived(
            events,
            models=models,
            linear_weights=linear_weights,
            boosted_model=boosted_model,
            scheduled=scheduled,
        )
        inserted, updated, derived_inserted = await self._upsert_tips(
            db,
            picks,
            competition_id=competition_id,
            season_id_by_event={
                e.event_id: e.season_id for e in [*events, *scheduled]
            },
        )
        # PARITY-1: the numeric model breakdown rides the SAME pass and
        # transaction â€” every league's match page serves the same
        # ``model_predictions`` section the AFL page does.  Failure is
        # contained (logged, empty counts) so tips never block on it.
        try:
            from .league_model_predictions import LeagueModelPredictionService

            prediction_summary = await LeagueModelPredictionService().generate_for_competition(
                db, competition_id=competition_id
            )
        except Exception:  # noqa: BLE001 â€” numbers never fail the tips pass
            logger.warning(
                "league model predictions failed for competition %s",
                competition_id,
                exc_info=True,
            )
            prediction_summary = {}
        await db.commit()
        summary = _summary(
            competition_id,
            [s.label for s in selected],
            len(events),
            inserted,
            updated,
            derived=derived_inserted,
            scheduled=len(scheduled),
        )
        if prediction_summary:
            summary["model_predictions"] = {
                "inserted": prediction_summary.get("predictions_inserted", 0),
                "updated": prediction_summary.get("predictions_updated", 0),
            }
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
    async def _fetch_competition_name(
        db: AsyncSession, competition_id: int
    ) -> Optional[str]:
        """The competition's NAME â€” the :data:`LEAGUE_MODEL_SETS` key (FCL-2).

        Deliberately a separate fetcher (not a wider identity fetch):
        the single-fetcher patch surface used by the service tests stays
        valid, and an unresolvable name falls through
        :func:`league_model_set_for_competition` to the sport resolution
        byte-identically, exactly like an unresolvable sport.
        """
        name = (
            await db.execute(
                select(Competition.name).where(Competition.id == competition_id)
            )
        ).scalar_one_or_none()
        return None if name is None else str(name)

    # -- learned-model loaders (patched out in unit tests) --------------

    @staticmethod
    async def _load_linear_weights(
        db: AsyncSession, *, competition_id: int
    ) -> Optional[tuple[float, dict[str, float]]]:
        """The competition's scoped active ``weighted_tip`` coefficients.

        FCL-4 scope: reads ONLY this competition's active version â€” a
        league never leaks another league's (or the global AFL's)
        weights.  ``None`` â€” no active scoped version yet, or a load
        failure â€” selects the majority-vote fallback (FCL-3d): tip
        generation never crashes on model load (FCL-6).  The CRUD import
        is deferred so this module's import surface stays unchanged.
        """
        from ..crud.model_versions import get_active_coefficients

        try:
            # FCL-11: the read runs inside a SAVEPOINT.  A failed load
            # (DB fault, un-migrated schema) otherwise leaves the
            # AsyncSession's transaction ABORTED â€” every later statement
            # of the generation pass (and the enclosing sync's fixture
            # writes!) then dies with PendingRollbackError.  Rolling
            # back to the savepoint discards only the failed read and
            # keeps the outer transaction â€” and the majority-vote
            # fallback â€” fully alive.
            async with db.begin_nested():
                return await get_active_coefficients(
                    db, HEURISTIC_WEIGHTED_TIP, competition_id=competition_id
                )
        except Exception:  # noqa: BLE001 â€” model load never fails generation
            logger.warning(
                "league heuristics: loading weighted_tip coefficients "
                "failed for competition %s â€” using the majority-vote "
                "fallback",
                competition_id,
                exc_info=True,
            )
            return None

    @staticmethod
    async def _load_boosted_artifact(
        db: AsyncSession, *, competition_id: int
    ) -> Optional[Any]:
        """The competition's boosted combiner, deserialized ONCE per pass.

        FCL-4 scope as for :func:`_load_linear_weights`; BT-2 on the
        shape â€” the artifact is deserialized HERE (not per event) so a
        season-wide pass pays the xgboost load once.  ``None`` â€” no
        scoped artifact, or a load/deserialize failure â€” selects the
        majority-vote fallback (FCL-3d); generation never crashes on
        model load (FCL-6).  Deserialization is reached only when an
        artifact actually exists, keeping the no-artifact path free of
        the xgboost import (FCL-3b).
        """
        from ..crud.model_versions import get_active_model_artifact

        try:
            # FCL-11: SAVEPOINT around the DB read â€” same reasoning as
            # :meth:`_load_linear_weights` (a failed read must not abort
            # the generation pass's transaction).
            async with db.begin_nested():
                loaded = await get_active_model_artifact(
                    db, HEURISTIC_BOOSTED_TIP, competition_id=competition_id
                )
        except Exception:  # noqa: BLE001 â€” model load never fails generation
            logger.warning(
                "league heuristics: loading boosted_tip artifact failed "
                "for competition %s â€” using the majority-vote fallback",
                competition_id,
                exc_info=True,
            )
            return None
        if loaded is None:
            return None
        artifact, artifact_format, _version = loaded
        from .league_consensus import load_league_boosted_model

        try:
            return load_league_boosted_model(artifact, artifact_format)
        except Exception:  # noqa: BLE001 â€” a corrupt artifact is a fallback, not a crash
            logger.warning(
                "league heuristics: deserializing the boosted_tip artifact "
                "failed for competition %s â€” using the majority-vote "
                "fallback",
                competition_id,
                exc_info=True,
            )
            return None

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

        The inner joins drop n-side/missing-side rows â€” those can never
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
                    Event.venue,
                    Event.round_id,
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
                venue=row[9],
                round_id=row[10],
            )
            for row in rows
        ]

    @staticmethod
    async def _fetch_scheduled_events(
        db: AsyncSession, season_ids: Sequence[int]
    ) -> list[CompletedEvent]:
        """SCHEDULED fixtures with both sides, in the same snapshot shape
        (FCL-12 forward tips).

        Mirrors :meth:`_fetch_completed_events` with the completion filter
        flipped to ``completed IS False AND status == 'scheduled'`` â€” a
        cancelled/void fixture is never tipped.  Scores are ``NULL`` by
        definition; every pick function reads only PRIOR completed
        evidence, so a scheduled snapshot carries ``None`` scores without
        affecting any pick.  The inner joins drop n-side/missing-side
        rows exactly as for completed events.
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
                    Event.venue,
                    Event.round_id,
                )
                .join(home, and_(home.event_id == Event.id, home.side == "home"))
                .join(away, and_(away.event_id == Event.id, away.side == "away"))
                .where(
                    Event.season_id.in_([int(sid) for sid in season_ids]),
                    Event.completed.is_(False),
                    Event.status == "scheduled",
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
                completed=False,
                venue=row[9],
                round_id=row[10],
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

    @staticmethod
    async def _fetch_existing_tip_numbers(
        db: AsyncSession, event_ids: Sequence[int]
    ) -> tuple[dict[tuple[int, str], Optional[float]], dict[tuple[int, str], Optional[int]]]:
        """Persisted (confidence, margin) keyed (event_id, heuristic) â€”
        the FCL-14 diff halves: a changed number is a changed tip."""
        ids = [int(eid) for eid in event_ids]
        if not ids:
            return {}, {}
        rows = (
            await db.execute(
                select(
                    LeagueTip.event_id,
                    LeagueTip.heuristic,
                    LeagueTip.confidence,
                    LeagueTip.margin,
                ).where(LeagueTip.event_id.in_(ids))
            )
        ).all()
        confidence = {(int(r[0]), str(r[1])): r[2] for r in rows}
        margin = {(int(r[0]), str(r[1])): r[3] for r in rows}
        return confidence, margin

    # -- persistence ------------------------------------------------------

    async def _upsert_tips(
        self,
        db: AsyncSession,
        picks: Sequence[TipPick],
        *,
        competition_id: int,
        season_id_by_event: dict[int, int],
    ) -> tuple[int, int, dict[str, int]]:
        """Diff picks against stored tips; insert new, update changed.

        Unchanged picks are untouched (no churn on re-sync).  The
        UNIQUE ``(event_id, heuristic)`` constraint is honoured by
        construction â€” one pick per key, new keys only inserted.
        FCL-6: returns ``(inserted, updated, inserted_by_heuristic)``
        â€” the third element breaks the insert count out per heuristic
        name so the generation summary can report derived-tip counts.
        """
        event_ids = [p.event_id for p in picks]
        existing = await self._fetch_existing_tips(db, event_ids)
        existing_confidence, existing_margin = await self._fetch_existing_tip_numbers(
            db, event_ids
        )
        inserted = 0
        updated = 0
        inserted_by_heuristic: dict[str, int] = {}
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
                        # FCL-14: the AFL card shape rides on the row.
                        confidence=pick.confidence,
                        margin=pick.margin,
                    )
                )
                inserted += 1
                inserted_by_heuristic[pick.heuristic] = (
                    inserted_by_heuristic.get(pick.heuristic, 0) + 1
                )
            elif (
                existing[key] != pick.selected_side_id
                or existing_confidence.get(key) != pick.confidence
                or existing_margin.get(key) != pick.margin
            ):
                if await self._update_tip(
                    db,
                    pick.event_id,
                    pick.heuristic,
                    pick.selected_side_id,
                    pick.confidence,
                    pick.margin,
                ):
                    updated += 1
        await db.flush()
        return inserted, updated, inserted_by_heuristic

    @staticmethod
    async def _update_tip(
        db: AsyncSession,
        event_id: int,
        heuristic: str,
        selected_side_id: Optional[int],
        confidence: Optional[float] = None,
        margin: Optional[int] = None,
    ) -> bool:
        """Point one stored tip at its recomputed selection (score
        revision); FCL-14 also refreshes the AFL card numbers."""
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
        tip.confidence = confidence
        tip.margin = margin
        return True


async def generate_league_tips(
    db: AsyncSession, *, competition_id: int
) -> dict[str, Any]:
    """Module-level entry point â€” the local_competition_sync hook target."""
    return await LeagueHeuristicsService().generate_for_competition(
        db, competition_id=competition_id
    )


async def league_round_tips(
    db: AsyncSession, *, competition_id: int, round_id: int
) -> dict[str, Any]:
    """The round's fixtures with every stored league pick â€” read-only
    bulk lookup for the league round pages (FCL-12).

    Scoped to the competition's CURRENT season (round numbers repeat
    across seasons) and ``round_id``.  Returns ``{"season": label,
    "fixtures": [...]}`` where each fixture carries both side names,
    the event slug/status and ``picks`` â€” ``{heuristic: participant
    name or None}`` in canonical heuristic order (``None`` =
    draw-no-pick).  NO AI explanations: the round page renders picks
    only, so a page view never pays the LLM pipeline (the per-event
    explanation endpoint stays the match page's job).  Fixtures without
    tips yet (never generated / no sides) appear with empty ``picks``.
    """
    season_row = (
        await db.execute(
            select(Season.id, Season.label)
            .where(Season.competition_id == competition_id)
            .order_by(Season.is_current.desc(), Season.label.desc())
        )
    ).first()
    if season_row is None:
        return {"season": None, "fixtures": []}
    season_id, season_label = int(season_row[0]), str(season_row[1])

    home = aliased(EventParticipant)
    away = aliased(EventParticipant)
    home_p = aliased(Participant)
    away_p = aliased(Participant)
    event_rows = (
        await db.execute(
            select(
                Event.id,
                Event.slug,
                Event.round_id,
                Event.starts_at,
                Event.status,
                Event.completed,
                home.id,
                away.id,
                home_p.name,
                away_p.name,
            )
            .join(home, and_(home.event_id == Event.id, home.side == "home"))
            .join(away, and_(away.event_id == Event.id, away.side == "away"))
            .join(home_p, home_p.id == home.participant_id)
            .join(away_p, away_p.id == away.participant_id)
            .where(
                Event.season_id == season_id,
                Event.round_id == round_id,
            )
            .order_by(Event.starts_at, Event.id)
        )
    ).all()
    if not event_rows:
        return {"season": season_label, "fixtures": []}

    # FCL-13: the canonical pick-key set resolves through the FULL
    # registry (league â†’ sport â†’ default) â€” the same resolution
    # generation and the explanation service use.  The previous static
    # DEFAULT_MODEL_SET seed silently dropped rugby-league's elo/matchup
    # picks from this payload (they fell out of the `if heuristic in
    # by_name` filter), so an nrl round card could never honour them.
    identity = (
        await db.execute(
            select(Competition.name, Competition.sport_id).where(
                Competition.id == competition_id
            )
        )
    ).first()
    canonical = [
        *league_model_set_for_competition(
            str(identity[0]) if identity else None,
            str(identity[1]) if identity else None,
        ),
        *DERIVED_LEAGUE_HEURISTICS,
    ]
    event_ids = [int(r[0]) for r in event_rows]
    # selected_participant_id targets EVENT_PARTICIPANTS.id (the side
    # row), not participants.id â€” the pick's team name comes via the
    # side row's participant FK (same discipline as the explanation
    # service's _picked_name).
    picked_side = aliased(EventParticipant)
    picked_team = aliased(Participant)
    tip_rows = (
        await db.execute(
            select(
                LeagueTip.event_id,
                LeagueTip.heuristic,
                picked_team.name,
                # FCL-14: the AFL card numbers ride on the row.
                LeagueTip.confidence,
                LeagueTip.margin,
                # PARITY-1: so does the persisted AI explanation â€” the
                # round page renders the same explanation text the AFL
                # cards do, with zero per-view LLM cost (the text is
                # written by the nightly league NLP sweep).
                LeagueTip.explanation,
            )
            .outerjoin(
                picked_side, picked_side.id == LeagueTip.selected_participant_id
            )
            .outerjoin(picked_team, picked_team.id == picked_side.participant_id)
            .where(LeagueTip.event_id.in_(event_ids))
        )
    ).all()
    picks_by_event: dict[int, dict[str, dict[str, Any]]] = {}
    for (
        event_id,
        heuristic,
        picked_name,
        confidence,
        margin,
        explanation,
    ) in tip_rows:
        by_name = picks_by_event.setdefault(
            int(event_id),
            {
                h: {
                    "picked": None,
                    "confidence": None,
                    "margin": None,
                    "explanation": None,
                }
                for h in canonical
            },
        )
        if heuristic in by_name:
            by_name[heuristic] = {
                "picked": None if picked_name is None else str(picked_name),
                "confidence": None if confidence is None else float(confidence),
                "margin": None if margin is None else int(margin),
                "explanation": None if explanation is None else str(explanation),
            }

    fixtures = [
        {
            "event_id": int(event_id),
            "slug": str(slug),
            "round": int(event_round) if event_round is not None else None,
            "starts_at": starts_at,
            "status": str(status),
            "completed": bool(completed),
            "home_team": str(home_name),
            "away_team": str(away_name),
            "picks": picks_by_event.get(int(event_id), {}),
        }
        for (
            event_id,
            slug,
            event_round,
            starts_at,
            status,
            completed,
            _home_side_id,
            _away_side_id,
            home_name,
            away_name,
        ) in event_rows
    ]
    return {"season": season_label, "fixtures": fixtures}
