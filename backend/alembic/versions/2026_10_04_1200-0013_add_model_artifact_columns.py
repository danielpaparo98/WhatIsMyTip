"""add_model_artifact_columns — boosted_tip model blob + SHAP base (BT-1).

``model_versions`` previously stored only linear-model state (an
intercept plus one ``model_coefficients`` row per feature) for the
``weighted_tip`` heuristic.  The new ``boosted_tip`` heuristic (BT-1) is
an XGBoost gradient-boosted regressor whose trained ensemble cannot be
expressed as coefficient rows: it is persisted as an opaque byte blob
via ``get_booster().save_raw(raw_format="json")``.

This migration adds three NULLABLE columns so one table serves both
heuristics and every existing ``weighted_tip`` row is untouched:

- ``artifact``        — BYTEA, the serialized XGBoost model bytes
- ``artifact_format`` — String(16), serialization tag (e.g. ``json``)
- ``shap_base_value`` — Float, the TreeExplainer expected value (base)

For ``boosted_tip`` versions the existing ``model_coefficients`` rows
are OVERLOADED to store the global SHAP feature importance
(``coefficient = mean |SHAP value|``) instead of linear weights — same
table, same shape, so the existing coefficient-chart pipeline works
unchanged.

Nullable-by-design: no backfill is needed, a NULL ``artifact`` simply
means "this version has no serialized blob" (every ``weighted_tip``
version, and any ``boosted_tip`` version pre-first-retrain), which the
runtime already treats as "serve the deterministic fallback".

Revision ID: 0013_add_model_artifact_columns
Revises: 0012_game_odds
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013_add_model_artifact_columns"
down_revision: Union[str, None] = "0012_game_odds"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "model_versions",
        sa.Column("artifact", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("artifact_format", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "model_versions",
        sa.Column("shap_base_value", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # Drop in reverse order of creation; all three are nullable so no
    # data-migration guard is required.
    op.drop_column("model_versions", "shap_base_value")
    op.drop_column("model_versions", "artifact_format")
    op.drop_column("model_versions", "artifact")
