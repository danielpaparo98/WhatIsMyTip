"""Ingestion seam tests (P3-1): FeedProvider protocol, canonical DTOs,
and the Squiggle dialect.

The provider owns the vendor dialect (``hteam/ateam/hscore/ascore/
complete/year/round``, the 100-means-final sentinel, ISO-Z dates).
Everything downstream of a FeedProvider speaks canonical ``FixtureDTO``
and never sees a vendor field name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from packages.shared.ingestion import FixtureDTO, FeedProvider
from packages.shared.ingestion.squiggle_provider import (
    SquiggleProvider,
    fixture_from_squiggle,
)


# A realistic Squiggle payload (subset of fields the dialect consumes).
def _squiggle_game(**overrides):
    data = {
        "id": 9001,
        "year": 2026,
        "round": 5,
        "hteam": "Western Bulldogs",
        "ateam": "GWS",
        "hscore": 88,
        "ascore": 71,
        "venue": "Marvel Stadium",
        "date": "2026-04-18T10:00:00Z",
        "complete": 100,
    }
    data.update(overrides)
    return data


class TestSquiggleDialect:
    def test_complete_payload_maps_to_canonical(self):
        dto = fixture_from_squiggle(_squiggle_game())

        assert dto.source == "squiggle"
        assert dto.external_id == 9001
        assert dto.season == 2026
        assert dto.round_id == 5
        assert dto.home_participant == "Western Bulldogs"
        assert dto.away_participant == "GWS"
        assert dto.home_score == 88
        assert dto.away_score == 71
        assert dto.venue == "Marvel Stadium"
        assert dto.completed is True
        # ISO-Z date parsed into an aware datetime
        assert dto.starts_at == datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc)

    def test_complete_sentinel_zero_means_not_final(self):
        dto = fixture_from_squiggle(_squiggle_game(complete=0))
        assert dto.completed is False

    def test_missing_scores_stay_none(self):
        dto = fixture_from_squiggle(_squiggle_game(hscore=None, ascore=None))
        assert dto.home_score is None
        assert dto.away_score is None

    def test_tbc_teams_stay_none(self):
        dto = fixture_from_squiggle(_squiggle_game(hteam=None, ateam=None))
        assert dto.home_participant is None
        assert dto.away_participant is None

    def test_missing_date_is_none_not_crash(self):
        dto = fixture_from_squiggle(_squiggle_game(date=None))
        assert dto.starts_at is None

    def test_missing_season_round_defaults(self):
        dto = fixture_from_squiggle(_squiggle_game(year=None, round=None))
        assert dto.season == 0
        assert dto.round_id == 0


class TestFeedProviderProtocol:
    def test_squiggle_provider_satisfies_protocol(self):
        assert isinstance(SquiggleProvider(client=AsyncMock()), FeedProvider)

    @pytest.mark.asyncio
    async def test_provider_returns_canonical_dtos(self):
        client = AsyncMock()
        client.get_games = AsyncMock(return_value=[_squiggle_game()])

        provider = SquiggleProvider(client=client)
        fixtures = await provider.get_fixtures(2026)

        client.get_games.assert_awaited_once_with(
            year=2026, round=None, complete=None, start_date=None, end_date=None
        )
        assert len(fixtures) == 1
        assert isinstance(fixtures[0], FixtureDTO)
        assert fixtures[0].external_id == 9001

    def test_provider_sport_id(self):
        assert SquiggleProvider(client=AsyncMock()).sport_id == "afl"

    @pytest.mark.asyncio
    async def test_provider_get_fixture_single(self):
        client = AsyncMock()
        client.get_game = AsyncMock(return_value=_squiggle_game(id=777))

        provider = SquiggleProvider(client=client)
        dto = await provider.get_fixture(777)

        client.get_game.assert_awaited_once_with(777)
        assert dto is not None
        assert dto.external_id == 777
        assert dto.completed is True

    @pytest.mark.asyncio
    async def test_provider_get_fixture_missing_returns_none(self):
        client = AsyncMock()
        client.get_game = AsyncMock(return_value=None)

        provider = SquiggleProvider(client=client)
        assert await provider.get_fixture(404) is None
