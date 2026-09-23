"""Sport-pluggable ingestion layer (P3-1, ADR 0001).

``FeedProvider`` implementations own their vendor dialect and yield
canonical DTOs; everything downstream speaks DTO.
"""

from .base import FeedProvider
from .dto import FixtureDTO

__all__ = ["FeedProvider", "FixtureDTO"]
