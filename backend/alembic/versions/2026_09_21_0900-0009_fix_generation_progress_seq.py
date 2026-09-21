"""Resync the generation_progress id sequence.

OPS FIX (2026-09-21): explicit-id inserts (historic CSV/data loads) do
NOT advance Postgres sequences.  The ``generation_progress`` id
sequence ended up behind ``max(id)``, so every new progress row
collided on the primary key ("Key (id)=(17) already exists") and the
historic-refresh job aborted before doing any work.

This migration nudges the sequence past the current maximum.  Idempotent
by nature: setval to max(id) is a no-op when the sequence is already
correct.

Revision ID: 0009_fix_generation_progress_seq
Revises: 0008_match_reports
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_fix_generation_progress_seq"
down_revision: Union[str, None] = "0008_match_reports"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute(
        "SELECT setval("
        "  pg_get_serial_sequence('generation_progress', 'id'),"
        "  COALESCE((SELECT MAX(id) FROM generation_progress), 1)"
        ")"
    )


def downgrade() -> None:
    # Nothing to undo — a resynced sequence is always correct.
    pass
