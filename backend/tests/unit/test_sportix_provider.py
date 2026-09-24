"""SportixProvider — the generalized Sportix-platform client (Phase 5).

The WAFL official site is one tenant of the Sportix platform.  Any
other competition hosted on the same platform (WAFLW, Colts, Reserves
— or a different league's tenant entirely) is a constructor call, not
new code.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from packages.shared.ingestion.sportix_provider import SportixProvider
from packages.shared.ingestion.wafl_provider import WaflProvider

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "wafl", "sportix_matches_2026_league.json"
)

_SEASONS = [{"id": "s-2026", "name": "2026", "active": True}]
_DISCOVERY = {
    "season": {"id": "s-2026", "name": "2026", "slug": "2026"},
    "competitions": [
        {"id": "comp-x", "name": "WAFLW", "slug": "waflw"},
    ],
}


class TestSportixGeneralization:
    def test_wafl_provider_is_a_sportix_provider(self):
        assert isinstance(WaflProvider(), SportixProvider)
        assert WaflProvider().source == "sportix-wafl"
        assert WaflProvider().competition_name == "League"

    def test_custom_competition_and_source(self):
        async def fake_fetch(path, params=None):
            if path == "seasons":
                return _SEASONS
            if params and params.get("season_slug"):
                return _DISCOVERY
            with open(_FIXTURE, encoding="utf-8") as f:
                payload = json.load(f)
            # Rename the competition so the filter is actually exercised.
            payload["competitions"][0]["name"] = "WAFLW"
            return payload

        provider = SportixProvider(
            source="sportix-waflw",
            competition_name="WAFLW",
            fetch_json=fake_fetch,
        )
        assert provider.sport_id == "afl"
        assert provider.source == "sportix-waflw"

        fixtures = _run(provider.get_fixtures(2026))
        assert len(fixtures) == 96
        assert fixtures[0].source == "sportix-waflw"

    def test_unknown_competition_raises(self):
        async def fake_fetch(path, params=None):
            if path == "seasons":
                return _SEASONS
            return _DISCOVERY

        provider = SportixProvider(
            source="sportix-x", competition_name="Nonexistent", fetch_json=fake_fetch
        )
        with pytest.raises(ValueError, match="Nonexistent"):
            _run(provider.get_fixtures(2026))

    def test_default_headers_carry_credentials(self):
        provider = SportixProvider(
            source="sportix-x",
            competition_name="League",
            api_key="k-test",
            tenant_id="t-test",
            api_url="https://api.example/",
        )
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer k-test"
        assert headers["tenant-id"] == "t-test"
        assert "WhatIsMyTip" in headers["User-Agent"]


def _run(coro):
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
