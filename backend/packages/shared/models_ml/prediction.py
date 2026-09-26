"""The typed prediction contract (P2-1 — plan Phase 2, ADR 0001).

``Prediction`` is a NamedTuple — deliberately.  Win *probability* is the
only signal every sport shares; ``score_projection`` (a margin in
points, a strokes gap, a sets count) is sport-optional and becomes
meaningful only under a ``SportContext``.

Because it is a NamedTuple, legacy consumers keep unpacking

    winner, confidence, margin = await model.predict(game, db)

unchanged during the strangler cutover — the positional order matches
the legacy ``(winner, confidence, margin)`` tuple.  The implicit
tuple bridge is documented as transitional and removed in Phase 4
when consumers are explicit.

``Abstained`` is the explicit non-exception abstention: a model with
no usable opinion returns the ``ABSTAINED`` singleton instead of
inventing a confident-looking default.  Raising remains reserved for
internal errors (the orchestrator records those as failed abstentions).
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Union


class Prediction(NamedTuple):
    """A model's or heuristic's opinion about one event.

    Attribute names are the sport-generic contract; positional order is
    the legacy ``(winner, confidence, margin)`` for unpack compatibility.
    """

    pick: str
    probability: float
    score_projection: Optional[float] = None


class Abstained:
    """Singleton marker: "this model has no usable opinion for this event."

    Always compare with ``is ABSTAINED``.  Falsy by design so stray
    truthiness checks behave sensibly.
    """

    _instance: Optional["Abstained"] = None

    def __new__(cls) -> "Abstained":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "ABSTAINED"

    def __bool__(self) -> bool:
        return False


ABSTAINED = Abstained()

# The ABC-level result contract.
ModelResult = Union[Prediction, Abstained]


def is_abstained(result: object) -> bool:
    """True for the ABSTAINED singleton (or legacy ``None`` abstentions)."""
    return result is None or isinstance(result, Abstained)


__all__ = ["Prediction", "Abstained", "ABSTAINED", "ModelResult", "is_abstained"]
