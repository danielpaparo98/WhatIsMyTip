"""Widen alembic_version.version_num to VARCHAR(128)

Alembic creates its bookkeeping table ``alembic_version`` with a single
column ``version_num VARCHAR(32)``.  This repo's revision ids are longer
than 32 characters (e.g. ``0003_job_executions_metrics_index`` is 34,
``0005_model_versions_coefficients`` is 33), so the production and
from-scratch upgrade paths must run with a wider column or alembic raises
``value too long for type character varying(32)`` the moment a long
revision id is stamped.

The live DB had this widened manually (``ALTER ... TYPE VARCHAR(128)``)
but the change was never captured in the tracked migration chain, so dev
and staging still ship the narrow ``VARCHAR(32)``.  This migration brings
the tracked chain in line with production.

Note: this migration alone does **not** fix a from-scratch
``alembic upgrade head`` — the long revision id ``0003_...`` is stamped
*before* this migration runs.  The from-scratch fix is the bootstrap guard
in ``backend/alembic/env.py`` (``_ensure_alembic_version_table_width``),
which widens (or creates) the column before ``context.run_migrations()``.
This migration exists so that, after the guard has done its job, every DB
ends up at ``VARCHAR(128)`` as a tracked, reviewable state.

Revision ID: 0006_model_version_num_width
Revises: 0005_model_versions_coefficients
Create Date: 2026-06-22 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "0006_model_version_num_width"
down_revision: Union[str, Sequence[str], None] = "0005_model_versions_coefficients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen ``alembic_version.version_num`` to ``VARCHAR(128)``.

    ``USING version_num::VARCHAR(128)`` is implicit for a plain widening
    cast, so a bare ``ALTER COLUMN ... TYPE`` is sufficient and safe
    against existing stamped rows.  Idempotent: widening an already-128
    column is a no-op.
    """
    op.execute(
        text(
            "ALTER TABLE alembic_version "
            "ALTER COLUMN version_num TYPE VARCHAR(128)"
        )
    )


def downgrade() -> None:
    """Safe no-op.

    The column cannot be narrowed back to ``VARCHAR(32)`` because the
    current head revision id (``0006_model_version_num_width`` and its
    predecessors ``0003_…`` / ``0005_…``) already exceeds 32 characters;
    shrinking would make alembic unable to read its own ``alembic_version``
    row.  Widening is a one-way, intentionally irreversible schema change.
    """
    pass
