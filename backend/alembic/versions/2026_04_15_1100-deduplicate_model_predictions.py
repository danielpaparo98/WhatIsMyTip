"""Deduplicate model predictions with inconsistent name casing.

The ML model get_name() methods previously returned PascalCase names
(e.g. "Elo", "HomeAdvantage") while the frontend expected lowercase
snake_case (e.g. "elo", "home_advantage"). Running generation multiple
times created duplicate rows because PostgreSQL unique constraints are
case-sensitive, so "Elo" and "elo" coexisted.

This migration:
1. Deletes rows with PascalCase names when a lowercase equivalent exists
2. Updates remaining PascalCase names to lowercase snake_case

Revision ID: dedup_model_preds
Revises: 8b7a6014748b
Create Date: 2026-04-15 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "dedup_model_preds"
down_revision = "8b7a6014748b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Deduplicate model predictions and normalise names to lowercase."""

    # Step 1: Delete PascalCase duplicates where a lowercase version exists.
    # We use a subquery to find the lowercase counterpart and delete the
    # PascalCase row, keeping the one that matches the canonical name.
    op.execute("""
        DELETE FROM model_predictions
        WHERE id IN (
            SELECT pascal.id
            FROM model_predictions pascal
            JOIN model_predictions lower ON (
                lower.game_id = pascal.game_id
                AND lower.model_name = CASE pascal.model_name
                    WHEN 'Elo' THEN 'elo'
                    WHEN 'Form' THEN 'form'
                    WHEN 'HomeAdvantage' THEN 'home_advantage'
                    WHEN 'Value' THEN 'value'
                END
            )
        )
    """)

    # Step 2: Rename any remaining PascalCase names to lowercase snake_case.
    op.execute("""
        UPDATE model_predictions
        SET model_name = CASE model_name
            WHEN 'Elo' THEN 'elo'
            WHEN 'Form' THEN 'form'
            WHEN 'HomeAdvantage' THEN 'home_advantage'
            WHEN 'Value' THEN 'value'
            ELSE model_name
        END
        WHERE model_name IN ('Elo', 'Form', 'HomeAdvantage', 'Value')
    """)


def downgrade() -> None:
    """Reverse the name normalisation (not recommended)."""
    op.execute("""
        UPDATE model_predictions
        SET model_name = CASE model_name
            WHEN 'elo' THEN 'Elo'
            WHEN 'form' THEN 'Form'
            WHEN 'home_advantage' THEN 'HomeAdvantage'
            WHEN 'value' THEN 'Value'
            ELSE model_name
        END
        WHERE model_name IN ('elo', 'form', 'home_advantage', 'value')
    """)
