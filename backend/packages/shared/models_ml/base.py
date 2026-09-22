from abc import ABC, abstractmethod
from typing import Union

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game
from .prediction import Abstained, Prediction


class BaseModel(ABC):
    """Base class for all prediction models.

    P2-1 contract: ``predict`` returns a :class:`Prediction` — or the
    ``ABSTAINED`` singleton when the model has no usable opinion for
    the event.  ``Prediction`` is a NamedTuple whose positional order
    matches the legacy ``(winner, confidence, margin)`` tuple, so
    consumers keep unpacking unchanged during the cutover.

    Raising is reserved for *internal errors* — the orchestrator
    records those as failed abstentions.  A model that simply lacks an
    opinion (insufficient data, sport mismatch) returns ``ABSTAINED``.
    """

    @abstractmethod
    async def predict(self, game: Game, db: AsyncSession) -> Union[Prediction, Abstained]:
        """Predict the outcome for a game.

        Args:
            game: Game to predict
            db: Database session to use for queries

        Returns:
            Prediction (pick, probability, score_projection?) or ABSTAINED
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """Get model name."""
        pass
