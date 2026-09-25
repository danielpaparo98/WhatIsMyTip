"""WaflProvider tests — driven by a REAL Sportix API response captured
from wafl.com.au (2026 League season, 96 matches) and stored as a
fixture, so parser tests don't touch the network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from packages.shared.ingestion.wafl_provider import WaflProvider

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "wafl", "sportix_matches_2026_league.json"
)

_SEASONS = [{"id": "s-2026", "name": "2026", "active": True}]
_DISCOVERY = {
    "season": {"id": "s-2026", "name": "2026", "slug": "2026"},
    "competitions": [
        {"id": "comp-league", "name": "League", "slug": "league"},
        {"id": "comp-colts", "name": "Colts", "slug": "colts"},
    ],
}


def _full_payload():
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _make_provider() -> tuple[WaflProvider, dict]:
    """Provider with a recording fake fetch wired to the real fixture."""
    calls: list = []
    payload = _full_payload()

    async def fake_fetch(path: str, params: dict | None = None):
        calls.append((path, params or {}))
        if path == "seasons":
            return _SEASONS
        if path == "matches" and params and params.get("season_slug"):
            return _DISCOVERY
        if path == "matches" and params and params.get("round") == "all":
            return payload
        raise AssertionError(f"unexpected call: {path} {params}")

    return WaflProvider(fetch_json=fake_fetch), calls


class TestWaflProvider:
    @pytest.mark.asyncio
    async def test_parses_the_real_2026_season(self):
        provider, calls = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        # The real 2026 WAFL League season has 96 matches (no byes).
        assert len(fixtures) == 96
        # Discovery + full fetch: two competition-scoped calls.
        assert any(c[0] == "seasons" for c in calls)
        assert any(
            c[0] == "matches" and c[1].get("round") == "all" for c in calls
        )

    @pytest.mark.asyncio
    async def test_first_round_1_match_maps_correctly(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        first = next(
            f for f in fixtures if f.round_id == 1 and f.home_participant == "Peel Thunder"
        )
        assert first.source == "sportix-wafl"
        assert first.external_id == "08f67d87-9423-4430-935a-c8f5a492af86"
        assert first.away_participant == "East Fremantle"
        assert first.home_score == 91
        assert first.away_score == 78
        assert first.completed is True
        assert first.venue == "Lane Group Stadium"
        # UTC-aware start time (converted to venue-local by the sync)
        assert first.starts_at == datetime(2026, 4, 3, 5, 10, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_grand_final_maps_to_max_round(self):
        """Finals map past the home-and-away rounds so the
        max(round) grand-final heuristic keeps working."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        gf = next(f for f in fixtures if f.round_id == 104)
        assert "grand-final" in str(gf.external_id) or gf.completed
        assert gf.home_participant == "Peel Thunder"
        assert gf.away_participant == "Claremont"
        # home-and-away rounds never collide with finals numbering
        assert max(f.round_id for f in fixtures) == 104

    @pytest.mark.asyncio
    async def test_all_round_ids_are_ordered_and_present(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        round_ids = {f.round_id for f in fixtures}
        assert round_ids == set(range(1, 21)) | {101, 102, 103, 104}

    @pytest.mark.asyncio
    async def test_byes_are_skipped(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)
        # A bye has no teams — none of the 96 stored matches may lack them.
        assert all(f.home_participant and f.away_participant for f in fixtures)

    def test_sport_id(self):
        provider, _ = _make_provider()
        assert provider.sport_id == "afl"
