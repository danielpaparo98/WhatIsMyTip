"""Add composite index for /api/admin/metrics (HI-006)

Adds ``ix_job_executions_job_name_started_at`` on
``job_executions(job_name, started_at DESC)``.  The metrics endpoint
queries ``job_executions`` seven times per job per request; without
this composite index the planner falls back to a sequential scan
once the table grows past a few thousand rows.

The DESC on ``started_at`` lets the planner do an index-only scan
for ``ORDER BY started_at DESC LIMIT 1`` (last-run / last-success /
last-failure lookups), which is the dominant access pattern.

Revision ID: 0003_metrics_index
Revises: 0002_weather_players_injuries
Create Date: 2026-06-18 01:45:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003_metrics_index"
down_revision: Union[str, Sequence[str], None] = (
    "0002_weather_players_injuries"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create composite index on ``job_executions(job_name, started_at DESC)``."""
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_executions_job_name_started_at "
        "ON job_executions (job_name, started_at DESC)"
    )


def downgrade() -> None:
    """Drop composite index on ``job_executions(job_name, started_at DESC)``."""
    op.execute(
        "DROP INDEX IF EXISTS ix_job_executions_job_name_started_at"
    )