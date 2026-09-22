"""consolidated_multisport — ADR 0001 target schema + AFL data copy.

Creates the sport-generic domain model (sports/competitions/seasons/
participants/events/…) and copies the existing AFL data into it:

* ``games``            → ``events`` + ``event_participants`` + source refs
* ``elo_cache``        → ``rating_snapshots``
* team names           → ``participants`` (kind='team'), canonicalized

STAGING (ADR 0001 / D2): legacy tables are NOT dropped and remain the
live read/write path until the service cutover (Phases 2–3).  This
migration only ADDS tables and INSERTS rows — it takes no locks on
legacy tables, keeping the in-process startup migration safe.

Explicit ids are copied for ``events`` (id == games.id) so event
children map 1:1; every copied sequence is setval'd past max(id) —
the 0009 lesson (explicit-id inserts do not advance sequences).

Revision ID: 0010_consolidated_multisport
Revises: 0009_fix_generation_progress_seq
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010_consolidated_multisport"
down_revision: Union[str, None] = "0009_fix_generation_progress_seq"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

# ---------------------------------------------------------------------------


def _create_schema() -> None:
    op.create_table(
        "sports",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sports")),
    )

    op.create_table(
        "competitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport_id", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False, server_default="national"),
        sa.Column("format", sa.String(length=20), nullable=False, server_default="rounds"),
        sa.Column("timezone", sa.String(length=50), nullable=False, server_default="Australia/Perth"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["sport_id"], ["sports.id"], name=op.f("fk_competitions_sport_id_sports")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitions")),
        sa.CheckConstraint("tier IN ('national', 'state', 'local')", name=op.f("ck_competitions_tier_valid")),
        sa.CheckConstraint("format IN ('rounds', 'tournament')", name=op.f("ck_competitions_format_valid")),
        sa.UniqueConstraint("sport_id", "name", name=op.f("uq_competitions_sport_name")),
    )
    op.create_index(op.f("ix_competitions_id"), "competitions", ["id"], unique=True)
    op.create_index(op.f("ix_competitions_sport_id"), "competitions", ["sport_id"], unique=False)

    op.create_table(
        "seasons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], name=op.f("fk_seasons_competition_id_competitions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seasons")),
        sa.UniqueConstraint("competition_id", "label", name=op.f("uq_seasons_competition_label")),
    )
    op.create_index(op.f("ix_seasons_id"), "seasons", ["id"], unique=True)
    op.create_index(op.f("ix_seasons_competition_id"), "seasons", ["competition_id"], unique=False)

    op.create_table(
        "participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport_id", sa.String(length=30), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["sport_id"], ["sports.id"], name=op.f("fk_participants_sport_id_sports")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participants")),
        sa.CheckConstraint("kind IN ('team', 'individual')", name=op.f("ck_participants_kind_valid")),
    )
    op.create_index(op.f("ix_participants_id"), "participants", ["id"], unique=True)
    op.create_index(op.f("ix_participants_sport_id"), "participants", ["sport_id"], unique=False)
    op.create_index(
        "uq_participants_sport_team_name",
        "participants",
        ["sport_id", "name"],
        unique=True,
        postgresql_where=sa.text("kind = 'team'"),
    )

    op.create_table(
        "teams",
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("abbreviation", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["participants.id"], name=op.f("fk_teams_participant_id_participants")
        ),
        sa.PrimaryKeyConstraint("participant_id", name=op.f("pk_teams")),
    )

    op.create_table(
        "individuals",
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("dob", sa.Date(), nullable=True),
        sa.Column("height_cm", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["participants.id"], name=op.f("fk_individuals_participant_id_participants")
        ),
        sa.PrimaryKeyConstraint("participant_id", name=op.f("pk_individuals")),
    )

    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_participant_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=150), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(
            ["team_participant_id"], ["participants.id"], name=op.f("fk_team_aliases_team_participant_id_participants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_aliases")),
        sa.UniqueConstraint("team_participant_id", "alias", name=op.f("uq_team_aliases_team_alias")),
    )
    op.create_index(op.f("ix_team_aliases_id"), "team_aliases", ["id"], unique=True)
    op.create_index(op.f("ix_team_aliases_team_participant_id"), "team_aliases", ["team_participant_id"], unique=False)
    op.create_index(op.f("ix_team_aliases_alias"), "team_aliases", ["alias"], unique=False)

    op.create_table(
        "rosters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_participant_id", sa.Integer(), nullable=False),
        sa.Column("individual_participant_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["team_participant_id"], ["participants.id"], name=op.f("fk_rosters_team_participant_id_participants")),
        sa.ForeignKeyConstraint(["individual_participant_id"], ["participants.id"], name=op.f("fk_rosters_individual_participant_id_participants")),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name=op.f("fk_rosters_season_id_seasons")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rosters")),
        sa.UniqueConstraint(
            "team_participant_id",
            "individual_participant_id",
            "season_id",
            name=op.f("uq_rosters_team_individual_season"),
        ),
    )
    op.create_index(op.f("ix_rosters_id"), "rosters", ["id"], unique=True)
    op.create_index(op.f("ix_rosters_team_participant_id"), "rosters", ["team_participant_id"], unique=False)
    op.create_index(op.f("ix_rosters_individual_participant_id"), "rosters", ["individual_participant_id"], unique=False)
    op.create_index(op.f("ix_rosters_season_id"), "rosters", ["season_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False, server_default="match"),
        sa.Column("round_id", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(length=150), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="scheduled"),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("slug", sa.String(length=16), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name=op.f("fk_events_season_id_seasons")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.CheckConstraint("event_type IN ('match', 'race', 'tournament_round')", name=op.f("ck_events_event_type_valid")),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled', 'void')", name=op.f("ck_events_status_valid")
        ),
        sa.UniqueConstraint(
            "season_id",
            "event_type",
            "round_id",
            "starts_at",
            "venue",
            name=op.f("uq_events_natural_key"),
        ),
    )
    op.create_index(op.f("ix_events_id"), "events", ["id"], unique=True)
    op.create_index(op.f("ix_events_season_id"), "events", ["season_id"], unique=False)
    op.create_index(op.f("ix_events_round_id"), "events", ["round_id"], unique=False)
    op.create_index(op.f("ix_events_starts_at"), "events", ["starts_at"], unique=False)
    op.create_index(op.f("ix_events_completed"), "events", ["completed"], unique=False)

    op.create_table(
        "event_participants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("is_winner", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name=op.f("fk_event_participants_event_id_events")),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], name=op.f("fk_event_participants_participant_id_participants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_participants")),
        sa.CheckConstraint("side IN ('home', 'away', 'n/a')", name=op.f("ck_event_participants_side_valid")),
        sa.UniqueConstraint("event_id", "participant_id", name=op.f("uq_event_participants_event_participant")),
    )
    op.create_index(op.f("ix_event_participants_id"), "event_participants", ["id"], unique=True)
    op.create_index(op.f("ix_event_participants_event_id"), "event_participants", ["event_id"], unique=False)
    op.create_index(op.f("ix_event_participants_participant_id"), "event_participants", ["participant_id"], unique=False)
    op.create_index(
        "uq_event_participants_event_side",
        "event_participants",
        ["event_id", "side"],
        unique=True,
        postgresql_where=sa.text("side <> 'n/a'"),
    )

    op.create_table(
        "event_source_refs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name=op.f("fk_event_source_refs_event_id_events")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_source_refs")),
        sa.UniqueConstraint("source", "external_id", name=op.f("uq_event_source_refs_source_external")),
    )
    op.create_index(op.f("ix_event_source_refs_id"), "event_source_refs", ["id"], unique=True)
    op.create_index(op.f("ix_event_source_refs_event_id"), "event_source_refs", ["event_id"], unique=False)

    op.create_table(
        "participant_source_refs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["participants.id"], name=op.f("fk_participant_source_refs_participant_id_participants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participant_source_refs")),
        sa.UniqueConstraint("source", "external_id", name=op.f("uq_participant_source_refs_source_external")),
    )
    op.create_index(op.f("ix_participant_source_refs_id"), "participant_source_refs", ["id"], unique=True)
    op.create_index(op.f("ix_participant_source_refs_participant_id"), "participant_source_refs", ["participant_id"], unique=False)

    op.create_table(
        "rating_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], name=op.f("fk_rating_snapshots_participant_id_participants")),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], name=op.f("fk_rating_snapshots_season_id_seasons")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rating_snapshots")),
        sa.UniqueConstraint("participant_id", "season_id", name=op.f("uq_rating_snapshots_participant_season")),
    )
    op.create_index(op.f("ix_rating_snapshots_id"), "rating_snapshots", ["id"], unique=True)
    op.create_index(op.f("ix_rating_snapshots_participant_id"), "rating_snapshots", ["participant_id"], unique=False)
    op.create_index(op.f("ix_rating_snapshots_season_id"), "rating_snapshots", ["season_id"], unique=False)

    op.create_table(
        "participant_match_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("stats", JSONB, nullable=False),
        sa.Column("stat_schema_version", sa.String(length=10), nullable=False, server_default="1"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name=op.f("fk_participant_match_stats_event_id_events")),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], name=op.f("fk_participant_match_stats_participant_id_participants")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participant_match_stats")),
        sa.UniqueConstraint("event_id", "participant_id", name=op.f("uq_participant_match_stats_event_participant")),
    )
    op.create_index(op.f("ix_participant_match_stats_id"), "participant_match_stats", ["id"], unique=True)
    op.create_index(op.f("ix_participant_match_stats_event_id"), "participant_match_stats", ["event_id"], unique=False)
    op.create_index(op.f("ix_participant_match_stats_participant_id"), "participant_match_stats", ["participant_id"], unique=False)


# ---------------------------------------------------------------------------
# AFL data copy
# ---------------------------------------------------------------------------

_AFL_COMPETITION_NAME = "Australian Football League"


def _copy_afl_data(conn: sa.Connection) -> dict:
    """Copy AFL rows from the legacy tables into the new model.

    Returns a small stats dict (useful in migration test assertions via
    the ``alembic`` output).
    """
    from packages.shared.teams import canonical_team

    stats = {"events": 0, "event_participants": 0, "source_refs": 0, "ratings": 0, "ratings_skipped": 0}

    conn.execute(sa.text(
        "INSERT INTO sports (id, display_name) VALUES ('afl', 'Australian Football') "
        "ON CONFLICT (id) DO NOTHING"
    ))
    competition_id = conn.execute(sa.text(
        "INSERT INTO competitions (sport_id, name, tier, format, timezone) "
        "VALUES ('afl', :name, 'national', 'rounds', 'Australia/Perth') "
        "ON CONFLICT (sport_id, name) DO NOTHING RETURNING id"
    ), {"name": _AFL_COMPETITION_NAME}).scalar()
    if competition_id is None:
        competition_id = conn.execute(sa.text(
            "SELECT id FROM competitions WHERE sport_id = 'afl' AND name = :name"
        ), {"name": _AFL_COMPETITION_NAME}).scalar_one()

    # --- seasons (one per distinct legacy season year) -------------------
    season_ids: dict[int, int] = {}
    for (season,) in conn.execute(sa.text(
        "SELECT DISTINCT season FROM games WHERE season IS NOT NULL ORDER BY season"
    )):
        season_id = conn.execute(sa.text(
            "INSERT INTO seasons (competition_id, label) VALUES (:cid, :label) "
            "ON CONFLICT (competition_id, label) DO NOTHING RETURNING id"
        ), {"cid": competition_id, "label": str(season)}).scalar()
        if season_id is None:
            season_id = conn.execute(sa.text(
                "SELECT id FROM seasons WHERE competition_id = :cid AND label = :label"
            ), {"cid": competition_id, "label": str(season)}).scalar_one()
        season_ids[season] = season_id

    # --- team participants (canonicalized, deduplicated) -----------------
    participant_ids: dict[str, int] = {}
    raw_names: set[str] = set()
    for row in conn.execute(sa.text("SELECT DISTINCT home_team, away_team FROM games")):
        for raw in (row[0], row[1]):
            canonical = canonical_team(raw)
            if canonical:
                raw_names.add(canonical)
    for name in sorted(raw_names):
        participant_id = conn.execute(sa.text(
            "INSERT INTO participants (sport_id, kind, name) "
            "VALUES ('afl', 'team', :name) RETURNING id"
        ), {"name": name}).scalar_one()
        participant_ids[name] = participant_id

    # --- events + event_participants + source refs ------------------------
    games = conn.execute(sa.text(
        "SELECT id, season, round_id, venue, date, completed, slug, "
        "       last_synced_at, sync_version, home_team, away_team, "
        "       home_score, away_score, squiggle_id, afltables_match_id "
        "FROM games ORDER BY id"
    )).mappings().all()

    for g in games:
        season_id = season_ids.get(g["season"])
        if season_id is None:
            continue
        event_id = conn.execute(sa.text(
            "INSERT INTO events (id, season_id, event_type, round_id, venue, "
            "                    starts_at, status, completed, slug, "
            "                    last_synced_at, sync_version) "
            "VALUES (:id, :season_id, 'match', :round_id, :venue, :starts_at, "
            "        :status, :completed, :slug, :last_synced_at, :sync_version) "
            "ON CONFLICT (season_id, event_type, round_id, starts_at, venue) "
            "DO NOTHING RETURNING id"
        ), {
            "id": g["id"],
            "season_id": season_id,
            "round_id": g["round_id"],
            "venue": g["venue"],
            "starts_at": g["date"],
            "status": "completed" if g["completed"] else "scheduled",
            "completed": bool(g["completed"]),
            "slug": g["slug"],
            "last_synced_at": g["last_synced_at"],
            "sync_version": g["sync_version"] or 0,
        }).scalar()
        if event_id is None:
            # A duplicate of an already-copied fixture (Squiggle re-publish)
            # — fold it into the existing event.
            event_id = conn.execute(sa.text(
                "SELECT id FROM events "
                "WHERE season_id = :season_id AND event_type = 'match' "
                "  AND round_id IS NOT DISTINCT FROM :round_id "
                "  AND starts_at IS NOT DISTINCT FROM :starts_at "
                "  AND venue IS NOT DISTINCT FROM :venue"
            ), {
                "season_id": season_id,
                "round_id": g["round_id"],
                "starts_at": g["date"],
                "venue": g["venue"],
            }).scalar()
            if event_id is None:
                continue
        stats["events"] += 1

        sides = [
            ("home", canonical_team(g["home_team"]), g["home_score"]),
            ("away", canonical_team(g["away_team"]), g["away_score"]),
        ]
        known_scores = [s for s in (g["home_score"], g["away_score"]) if s is not None]
        has_result = len(known_scores) == 2 and known_scores[0] != known_scores[1]
        winning_side = None
        if has_result:
            winning_side = "home" if g["home_score"] > g["away_score"] else "away"

        for side, name, score in sides:
            participant_id = participant_ids.get(name)
            if participant_id is None:
                continue  # TBC fixture side — no participant identity yet
            conn.execute(sa.text(
                "INSERT INTO event_participants (event_id, participant_id, side, score, is_winner) "
                "VALUES (:eid, :pid, :side, :score, :is_winner) "
                "ON CONFLICT (event_id, participant_id) DO NOTHING"
            ), {
                "eid": event_id,
                "pid": participant_id,
                "side": side,
                "score": score,
                "is_winner": (side == winning_side) if has_result else None,
            })
            stats["event_participants"] += 1

        for source, external in (
            ("squiggle", g["squiggle_id"]),
            ("afltables", g["afltables_match_id"]),
        ):
            if external is None:
                continue
            conn.execute(sa.text(
                "INSERT INTO event_source_refs (event_id, source, external_id) "
                "VALUES (:eid, :source, :external) "
                "ON CONFLICT (source, external_id) DO NOTHING"
            ), {"eid": event_id, "source": source, "external": str(external)})
            stats["source_refs"] += 1

    # --- elo_cache → rating_snapshots -------------------------------------
    for r in conn.execute(sa.text(
        "SELECT team_name, rating, season FROM elo_cache"
    )).mappings():
        participant_id = participant_ids.get(canonical_team(r["team_name"]))
        season_id = season_ids.get(r["season"]) if r["season"] is not None else None
        if participant_id is None or season_id is None:
            stats["ratings_skipped"] += 1
            continue
        conn.execute(sa.text(
            "INSERT INTO rating_snapshots (participant_id, season_id, rating) "
            "VALUES (:pid, :sid, :rating) "
            "ON CONFLICT (participant_id, season_id) DO NOTHING"
        ), {"pid": participant_id, "sid": season_id, "rating": r["rating"]})
        stats["ratings"] += 1

    # --- 0009 lesson: explicit-id copies do not advance sequences ---------
    for table in (
        "participants",
        "events",
        "event_participants",
        "event_source_refs",
        "participant_source_refs",
        "rating_snapshots",
        "team_aliases",
        "rosters",
        "participant_match_stats",
        "seasons",
    ):
        conn.execute(sa.text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        ))

    return stats


def upgrade() -> None:
    _create_schema()
    conn = op.get_bind()
    _copy_afl_data(conn)


def downgrade() -> None:
    # Reverse FK-dependency order; legacy tables are untouched (ADR 0001).
    op.drop_table("participant_match_stats")
    op.drop_table("rating_snapshots")
    op.drop_table("participant_source_refs")
    op.drop_table("event_source_refs")
    op.drop_table("event_participants")
    op.drop_table("events")
    op.drop_table("rosters")
    op.drop_table("team_aliases")
    op.drop_table("individuals")
    op.drop_table("teams")
    op.drop_table("participants")
    op.drop_table("seasons")
    op.drop_table("competitions")
    op.drop_table("sports")
