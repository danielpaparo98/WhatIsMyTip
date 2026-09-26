"""team_identity — capture team logo URLs and colours at ingestion.

Adds three nullable identity columns to ``teams`` (ADR 0001 staging):
feeds supply what they supply — the AFL platform exposes no
logo/colour data at all, iSports carries logo filenames against a
stable S3 base, and Sportix club crests are content-hashed and
unstable — so everything is optional and the frontend keeps its
hard-coded AFL maps as the fallback.

Revision ID: 0011_team_identity
Revises: 0010_consolidated_multisport
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011_team_identity"
down_revision: Union[str, None] = "0010_consolidated_multisport"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("logo_url", sa.Text(), nullable=True))
    # '#RRGGBB' or '#RRGGBBAA' — 9 chars covers both.
    op.add_column(
        "teams", sa.Column("primary_color", sa.String(length=9), nullable=True)
    )
    op.add_column(
        "teams", sa.Column("secondary_color", sa.String(length=9), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("teams", "secondary_color")
    op.drop_column("teams", "primary_color")
    op.drop_column("teams", "logo_url")
