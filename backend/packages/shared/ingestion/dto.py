"""Canonical ingestion DTOs (P3-1, ADR 0001 / plan Phase 3).

A ``FixtureDTO`` is what a sport IS to the rest of the system: a
contest between two participants within a season, with an optional
venue/time and a source identity.  No vendor field names survive past
the :class:`~packages.shared.ingestion.base.FeedProvider` boundary —
the provider owns the dialect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class FixtureDTO:
    """One fixture as reported by a feed provider.

    ``home_participant``/``away_participant`` carry the provider's raw
    participant names — canonicalization is storage policy
    (``ParticipantResolver``), not a provider concern.  ``None``
    participants are TBC placeholders; ``None`` scores mean the game
    has no result yet.
    """

    source: str
    external_id: Optional[int]
    season: int
    round_id: Optional[int]
    home_participant: Optional[str]
    away_participant: Optional[str]
    home_score: Optional[int]
    away_score: Optional[int]
    venue: Optional[str]
    starts_at: Optional[datetime]
    completed: bool


__all__ = ["FixtureDTO"]
