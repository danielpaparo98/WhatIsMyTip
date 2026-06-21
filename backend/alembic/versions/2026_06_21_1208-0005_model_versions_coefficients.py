"""Add model_versions & model_coefficients, rename heuristic to weighted_tip

Introduces the persistence layer for the new ``weighted_tip`` ML model,
which learns optimal weights (via scikit-learn ``LinearRegression``) to
combine the outputs of the eight underlying per-feature models.  The
``weighted_tip`` heuristic replaces the legacy hand-tuned
``high_risk_high_reward`` heuristic; this migration:

1. Creates ``model_versions`` — one row per trained version of a named
   model (e.g. ``weighted_tip``), storing the intercept, training
   metadata, quality metrics (JSONB) and an ``is_active`` flag so the
   runtime can atomically promote a freshly retrained version.
2. Creates ``model_coefficients`` — the learned weight for each feature
   (the eight underlying model names), FK-linked to its version with
   ``ON DELETE CASCADE`` so retiring a version cleans up its weights.
3. Renames every stored occurrence of the old heuristic value
   ``high_risk_high_reward`` to ``weighted_tip`` in ``tips`` and
   ``backtest_results``.  The unique constraints on those tables
   (``uq_game_heuristic``, ``uq_backtest_season_round_heuristic``) are
   defined over the column, not a fixed literal, so renaming the value
   is safe and requires no constraint rewrite.

The model is retrained weekly (Subtask 3); this migration only lays down
the schema and performs the one-off data rename.

Revision ID: 0005_model_versions_coefficients
Revises: 0004_canonical_team_names
Create Date: 2026-06-21 12:08:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "0005_model_versions_coefficients"
down_revision: Union[str, Sequence[str], None] = "0004_canonical_team_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Old -> new heuristic value applied to every table that stores it.
_OLD_HEURISTIC = "high_risk_high_reward"
_NEW_HEURISTIC = "weighted_tip"


def upgrade() -> None:
    """Create the model-version tables and rename the old heuristic value."""

    # ------------------------------------------------------------------
    # 1. model_versions
    #    One row per trained version of a named ML model.  ``is_active``
    #    marks the version the runtime should serve; promotion is done by
    #    flipping this flag so retraining never blocks reads.
    # ------------------------------------------------------------------
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "intercept",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "trained_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "training_rows",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_name", "version", name="uq_model_versions_name_version"
        ),
    )
    op.create_index("ix_model_versions_id", "model_versions", ["id"], unique=False)
    op.create_index(
        "ix_model_versions_model_active",
        "model_versions",
        ["model_name", "is_active"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 2. model_coefficients
    #    The learned weight for each feature of a given version.
    #    FK → model_versions.id with ON DELETE CASCADE so retiring a
    #    version automatically removes its weights.
    # ------------------------------------------------------------------
    op.create_table(
        "model_coefficients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("feature_name", sa.String(length=128), nullable=False),
        sa.Column("coefficient", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"], ["model_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_version_id",
            "feature_name",
            name="uq_model_coefficients_version_feature",
        ),
    )
    op.create_index(
        "ix_model_coefficients_id", "model_coefficients", ["id"], unique=False
    )
    op.create_index(
        "ix_model_coefficients_version",
        "model_coefficients",
        ["model_version_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 3. Rename the stored heuristic value high_risk_high_reward ->
    #    weighted_tip in every table that stores it.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE tips SET heuristic = :new WHERE heuristic = :old"
        ).bindparams(new=_NEW_HEURISTIC, old=_OLD_HEURISTIC)
    )
    bind.execute(
        text(
            "UPDATE backtest_results SET heuristic = :new WHERE heuristic = :old"
        ).bindparams(new=_NEW_HEURISTIC, old=_OLD_HEURISTIC)
    )


def downgrade() -> None:
    """Reverse the upgrade: revert the heuristic rename then drop the tables."""
    bind = op.get_bind()
    bind.execute(
        text(
            "UPDATE tips SET heuristic = :old WHERE heuristic = :new"
        ).bindparams(old=_OLD_HEURISTIC, new=_NEW_HEURISTIC)
    )
    bind.execute(
        text(
            "UPDATE backtest_results SET heuristic = :old WHERE heuristic = :new"
        ).bindparams(old=_OLD_HEURISTIC, new=_NEW_HEURISTIC)
    )

    # Drop child first (FK dependency), then parent.  ``drop_table``
    # cascades the associated indexes/constraints in PostgreSQL.
    op.drop_table("model_coefficients")
    op.drop_table("model_versions")
