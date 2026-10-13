"""model_versions competition scope — per-league model storage (FCL-1).

First-class league heuristics: every league gets its own boosted /
linear / best_bet heuristics, which need per-league *trained* models.
``model_versions`` previously stored only the legacy global AFL models
(one active ``weighted_tip`` / ``boosted_tip`` version each), so there
was nowhere to put a league's model state.

Following ADR 0001 D1 ("shared tables + scope columns"), this migration
adds a NULLABLE ``competition_id`` to ``model_versions`` instead of a
per-league table:

- ``NULL``  — legacy global model.  Every existing row keeps NULL, so
  the AFL pipeline (orchestrator, CRUD, weekly retrain) is untouched.
- non-NULL — version scoped to exactly one competition.

At most one ACTIVE version may exist per ``(model_name, scope)`` — the
weekly retrain promotes a new version by flipping ``is_active``, and
reads pick the single active row.  A plain UNIQUE constraint cannot
express this across two scopes (Postgres treats NULLs as distinct in
UNIQUE keys, and the active flag must be part of the predicate, not the
key), so two complementary PARTIAL UNIQUE indexes enforce it:

- ``uq_model_versions_global_active``      — ``(model_name)`` WHERE
  ``is_active AND competition_id IS NULL``
- ``uq_model_versions_competition_active`` — ``(model_name,
  competition_id)`` WHERE ``is_active AND competition_id IS NOT NULL``

The scopes are independent: a global active version coexists with one
active version per competition.  The pre-existing
``uq_model_versions_name_version`` uniqueness is preserved untouched.

The FK follows the repo convention for ``competitions`` references (no
ON DELETE — see ``league_tips.competition_id`` / ``seasons``), and the
column is indexed like every other FK column.

Revision ID: 0015_model_versions_competition_scope
Revises: 0014_create_league_tips
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0015_model_versions_competition_scope"
down_revision: Union[str, None] = "0014_create_league_tips"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Nullable-by-design: no backfill — NULL simply means "legacy global
    # model", which is what every existing row already is.
    op.add_column(
        "model_versions",
        sa.Column("competition_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_model_versions_competition_id_competitions"),
        "model_versions",
        "competitions",
        ["competition_id"],
        ["id"],
    )
    # Repo convention: FK columns are indexed (matches league_tips/seasons).
    op.create_index(
        op.f("ix_model_versions_competition_id"),
        "model_versions",
        ["competition_id"],
        unique=False,
    )
    # FCL-1: at most one ACTIVE version per (model_name, scope).  Two
    # partial unique indexes, one per scope, keep legacy global semantics
    # (NULL) and per-competition semantics (NOT NULL) independent.
    op.create_index(
        op.f("uq_model_versions_global_active"),
        "model_versions",
        ["model_name"],
        unique=True,
        postgresql_where=sa.text("is_active AND competition_id IS NULL"),
    )
    op.create_index(
        op.f("uq_model_versions_competition_active"),
        "model_versions",
        ["model_name", "competition_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND competition_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop in reverse order of creation: both partial unique indexes and
    # the FK index reference the column, and the FK constraint belongs to
    # it, so all four go before the column itself.
    op.drop_index(
        op.f("uq_model_versions_competition_active"), table_name="model_versions"
    )
    op.drop_index(
        op.f("uq_model_versions_global_active"), table_name="model_versions"
    )
    op.drop_index(
        op.f("ix_model_versions_competition_id"), table_name="model_versions"
    )
    op.drop_constraint(
        op.f("fk_model_versions_competition_id_competitions"),
        "model_versions",
        type_="foreignkey",
    )
    op.drop_column("model_versions", "competition_id")
