"""FeedProvider — the sport-pluggable ingestion protocol (P3-1).

Every data source (Squiggle, a rugby API, a tennis tour feed, a local
competition CSV) implements this protocol and owns its own vendor
dialect.  Downstream — services, CRUD, the future events pipeline —
speaks only canonical DTOs.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .dto import FixtureDTO


@runtime_checkable
class FeedProvider(Protocol):
    """A source of fixtures for one sport."""

    sport_id: str

    async def get_fixtures(
        self,
        season: int,
        *,
        round: int | None = None,
        complete: bool | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Sequence[FixtureDTO]:
        """Fetch fixtures for a season (optionally narrowed)."""
        ...

    async def get_fixture(self, external_id: int) -> FixtureDTO | None:
        """Fetch a single fixture by its provider-assigned id."""
        ...


__all__ = ["FeedProvider"]
