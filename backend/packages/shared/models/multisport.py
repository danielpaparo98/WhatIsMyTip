"""Sport-generic domain model (ADR 0001, migration 0010).

The unit of prediction is an **event between participants within a
competition**.  ``Participant.kind`` covers both teams and individuals,
which is what makes team sports *and* golf/tennis expressible without
per-sport tables.  Vendor identities live in ``*_source_refs`` side
tables; per-sport statistics live in JSONB with a schema version.

Staging (ADR 0001 / D2): these tables coexist with the legacy AFL tables
(``games``, ``tips``, …) until the services are cut over (Phases 2–3);
the legacy tables are dropped by a later migration.

Timezone policy: ``events.starts_at`` is timezone-naive (venue-local)
and is interpreted through ``competitions.timezone`` — this matches the
legacy ``games.date`` semantics without inventing conversions during
the data copy.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..db import Base


class Sport(Base):
    """A sport (``'afl'``, ``'rugby'``, ``'tennis'``, …)."""

    __tablename__ = "sports"

    id = Column(String(30), primary_key=True)
    display_name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Competition(Base):
    """A named competition within a sport, e.g. AFL vs a local league.

    ``tier`` distinguishes national leagues from state/local competitions
    (the first expansion target).  ``format`` distinguishes round-robin
    seasons from tournaments.  ``timezone`` interprets venue-local
    ``events.starts_at`` values and schedules.
    """

    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint(
            "tier IN ('national', 'state', 'local')", name="tier_valid"
        ),
        CheckConstraint(
            "format IN ('rounds', 'tournament')", name="format_valid"
        ),
        UniqueConstraint("sport_id", "name", name="uq_competitions_sport_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sport_id = Column(String(30), ForeignKey("sports.id"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    tier = Column(String(20), nullable=False, default="national")
    format = Column(String(20), nullable=False, default="rounds")
    timezone = Column(String(50), nullable=False, default="Australia/Perth")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Season(Base):
    """A season of a competition (``label`` e.g. ``'2026'``).

    Replaces the bare ``season`` integer scattered through the legacy
    tables — rugby/soccer seasons span years and golf's is a calendar
    year, so the label is competition-relative.
    """

    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("competition_id", "label", name="uq_seasons_competition_label"),
    )

    id = Column(Integer, primary_key=True, index=True)
    competition_id = Column(
        Integer, ForeignKey("competitions.id"), nullable=False, index=True
    )
    label = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_current = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Participant(Base):
    """A competitor — either a team or an individual (D3).

    Human names are not unique, so the partial unique index only
    constrains team names per sport.
    """

    __tablename__ = "participants"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('team', 'individual')", name="kind_valid"
        ),
        Index(
            "uq_participants_sport_team_name",
            "sport_id",
            "name",
            unique=True,
            postgresql_where=text("kind = 'team'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    sport_id = Column(String(30), ForeignKey("sports.id"), nullable=False, index=True)
    kind = Column(String(12), nullable=False)
    name = Column(String(150), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Team(Base):
    """Club-level extension of a team ``Participant``.

    Identity columns (migration 0011) are captured at ingestion when
    the feed supplies them — ``logo_url`` is a full URL, the colours
    are ``'#RRGGBB'``/``'#RRGGBBAA'`` strings.  They stay NULL for
    feeds without identity data; the frontend then falls back to its
    hard-coded AFL logo/colour maps.
    """

    __tablename__ = "teams"

    participant_id = Column(
        Integer, ForeignKey("participants.id"), primary_key=True
    )
    abbreviation = Column(String(10), nullable=True)
    logo_url = Column(Text, nullable=True)
    primary_color = Column(String(9), nullable=True)
    secondary_color = Column(String(9), nullable=True)


class Individual(Base):
    """Person-level extension of an individual ``Participant``.

    Typed biometrics — the legacy ``players.height/weight`` Text columns
    are gone.
    """

    __tablename__ = "individuals"

    participant_id = Column(
        Integer, ForeignKey("participants.id"), primary_key=True
    )
    dob = Column(Date, nullable=True)
    height_cm = Column(Integer, nullable=True)
    weight_kg = Column(Integer, nullable=True)


class TeamAlias(Base):
    """Alternate name for a team (``'Adelaide Crows'`` → ``'Adelaide'``).

    Replaces the four hard-coded copies of the canonical team map
    (``teams.py``, CSV loader, frontend logo map, logo downloader).
    """

    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint(
            "team_participant_id", "alias", name="uq_team_aliases_team_alias"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    team_participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    alias = Column(String(150), nullable=False, index=True)
    source = Column(String(30), nullable=True)


class Roster(Base):
    """An individual's membership of a team for a season."""

    __tablename__ = "rosters"
    __table_args__ = (
        UniqueConstraint(
            "team_participant_id",
            "individual_participant_id",
            "season_id",
            name="uq_rosters_team_individual_season",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    team_participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    individual_participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, index=True)
    position = Column(String(50), nullable=True)


class Event(Base):
    """A fixture: a match, race, or tournament round within a season.

    The natural key ``(season_id, event_type, round_id, starts_at, venue)``
    replaces the vendor-ID-only uniqueness of legacy ``games`` (which
    needed a DUP-GUARD workaround when Squiggle re-published fixtures).
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('match', 'race', 'tournament_round')",
            name="event_type_valid",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled', 'void')",
            name="status_valid",
        ),
        UniqueConstraint(
            "season_id",
            "event_type",
            "round_id",
            "starts_at",
            "venue",
            name="uq_events_natural_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, index=True)
    event_type = Column(String(20), nullable=False, default="match")
    # Round/week number for rounds-format competitions; NULL for
    # tournaments (golf) and race events.
    round_id = Column(Integer, nullable=True, index=True)
    venue = Column(String(150), nullable=True)
    # Venue-local naive timestamp — see module docstring for tz policy.
    starts_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(20), nullable=False, default="scheduled")
    completed = Column(Boolean, nullable=False, default=False, index=True)
    slug = Column(String(16), unique=True, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EventParticipant(Base):
    """One side of an event, with its score and result.

    Replaces ``games.home_team/away_team/home_score/away_score``.  For
    matches both a ``home`` and an ``away`` row exist (the partial unique
    index caps it at one each); ``n/a`` sides cover races and
    field-format events such as golf tournaments.
    """

    __tablename__ = "event_participants"
    __table_args__ = (
        CheckConstraint("side IN ('home', 'away', 'n/a')", name="side_valid"),
        UniqueConstraint(
            "event_id", "participant_id", name="uq_event_participants_event_participant"
        ),
        Index(
            "uq_event_participants_event_side",
            "event_id",
            "side",
            unique=True,
            postgresql_where=text("side <> 'n/a'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    side = Column(String(4), nullable=False)
    score = Column(Integer, nullable=True)
    # NULL for draws and unfinished events — a draw has no winner.
    is_winner = Column(Boolean, nullable=True)


class EventSourceRef(Base):
    """External provider identity for an event (``('squiggle', '9001')``).

    Vendor IDs live here, not on the domain table — any provider is
    pluggable and re-published fixtures resolve via the natural key.
    """

    __tablename__ = "event_source_refs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_event_source_refs_source_external"),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source = Column(String(30), nullable=False)
    external_id = Column(String(50), nullable=False)


class ParticipantSourceRef(Base):
    """External provider identity for a participant."""

    __tablename__ = "participant_source_refs"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", name="uq_participant_source_refs_source_external"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    source = Column(String(30), nullable=False)
    external_id = Column(String(50), nullable=False)


class RatingSnapshot(Base):
    """A strength rating (e.g. Elo) for a participant within a season.

    Keyed ``(participant_id, season_id)`` — structurally fixing the
    legacy ``elo_cache`` whose unique constraint on ``team_name`` alone
    allowed only one season of ratings per team and cross-contaminated
    sports by name.
    """

    __tablename__ = "rating_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "participant_id", "season_id", name="uq_rating_snapshots_participant_season"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, index=True)
    rating = Column(Float, nullable=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())


class ParticipantMatchStats(Base):
    """Per-event statistics for a participant as a versioned JSON payload.

    Typed AFL stat columns (kicks/handballs/…) do not survive sport #2;
    per-sport stat shapes are validated by versioned Pydantic schemas
    keyed on ``stat_schema_version`` (introduced in Phase 3).
    """

    __tablename__ = "participant_match_stats"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "participant_id", name="uq_participant_match_stats_event_participant"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id = Column(
        Integer, ForeignKey("participants.id"), nullable=False, index=True
    )
    stats = Column(JSONB, nullable=False)
    stat_schema_version = Column(String(10), nullable=False, default="1")
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "Sport",
    "Competition",
    "Season",
    "Participant",
    "Team",
    "Individual",
    "TeamAlias",
    "Roster",
    "Event",
    "EventParticipant",
    "EventSourceRef",
    "ParticipantSourceRef",
    "RatingSnapshot",
    "ParticipantMatchStats",
]
