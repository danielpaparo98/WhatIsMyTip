"""ORM ↔ database metadata integrity guards (P1-2).

The ORM metadata is the input to every future ``alembic --autogenerate``.
When it drifts from the migrated schema, autogen emits destructive
no-ops (e.g. dropping FKs that exist in the DB but not in the models).
These tests pin the invariants that keep autogen honest.
"""

from __future__ import annotations

from packages.shared.db import Base
from packages.shared.models import ModelPrediction, Tip


class TestForeignKeyDeclarations:
    """``tips.game_id`` / ``model_predictions.game_id`` have DB-side FKs
    (``ON DELETE CASCADE``, created in 0001) — the ORM must declare them
    too, or autogenerate will try to drop them."""

    @staticmethod
    def _fk_targets(column) -> set[tuple[str, str]]:
        return {
            (fk.constraint.referred_table.name, tuple(fk.constraint.column_keys)[0])
            for fk in column.foreign_keys
        }

    def test_tip_game_id_declares_fk(self):
        fks = self._fk_targets(Tip.__table__.c.game_id)
        assert ("games", "game_id") in fks

    def test_tip_game_id_fk_cascades(self):
        fk = next(iter(Tip.__table__.c.game_id.foreign_keys))
        assert fk.constraint.ondelete == "CASCADE"

    def test_model_prediction_game_id_declares_fk(self):
        fks = self._fk_targets(ModelPrediction.__table__.c.game_id)
        assert ("games", "game_id") in fks

    def test_model_prediction_game_id_fk_cascades(self):
        fk = next(iter(ModelPrediction.__table__.c.game_id.foreign_keys))
        assert fk.constraint.ondelete == "CASCADE"


class TestNamingConvention:
    """Deterministic constraint names — without a convention, ORM-created
    constraints get Postgres defaults while migration-authored ones get
    manual names, making every future autogen diff noisy (P1-2)."""

    def test_base_metadata_has_naming_convention(self):
        convention = Base.metadata.naming_convention
        for key in ("ix", "uq", "ck", "fk", "pk"):
            assert key in convention, f"naming_convention missing {key!r}"
