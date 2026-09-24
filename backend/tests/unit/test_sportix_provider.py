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

_TRIMMED_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "wafl", "sportix_matches_2026_trimmed.json"
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


class TestSportixTeamMetadata:
    """Migration 0011 follow-up: ``GET /public/clubs/{id}`` carries a
    ``logo`` path (verified live 2026-09-24 — the Peel Thunder capture
    from the original recon is STILL the served filename, so the hash
    is stable per club asset).  Crests are captured at sync time into
    ``{name: {logo_url}}``; every request reuses ``self._fetch_json``
    (the retrying fetch)."""

    def _provider(self, calls):
        club_logos = {
            "632ebb80-4bd7-11e9-9660-19fd5993277e": (
                "clubs/peel-thunder-145-LCdnaJ.png"
            ),
            "63274850-4bd7-11e9-a29d-bd0ecec49dc9": (
                "clubs/east-fremantle-146-LAbcde.png"
            ),
            "63326060-4bd7-11e9-995e-1be5c911bb6e": (
                "clubs/perth-147-KXyzuv.png"
            ),
            "63a5abf0-4bd7-11e9-acd2-39cf2c564a87": (
                "clubs/west-coast-148-MQrstu.png"
            ),
            # No crest on file → skipped from the mapping.
            "6327a370-4bd7-11e9-b51a-e14a4ceeb04d": None,
        }

        async def fake_fetch(path, params=None):
            calls.append((path, params))
            if path == "clubs/x-broken":
                raise RuntimeError("edge 522")
            if path.startswith("clubs/"):
                club_id = path.split("/", 1)[1]
                logo = club_logos.get(club_id)
                return {"id": club_id, "name": "irrelevant", "logo": logo}
            with open(_TRIMMED_FIXTURE, encoding="utf-8") as f:
                return json.load(f)

        return SportixProvider(
            source="sportix-wafl",
            competition_name="League",
            fetch_json=fake_fetch,
        )

    def test_metadata_maps_names_to_cdn_logo_urls(self):
        calls = []
        metadata = _run(self._provider(calls).get_team_metadata(2026))

        base = "https://storage-cdn.sportix.cloud"
        assert metadata == {
            "Peel Thunder": {"logo_url": f"{base}/clubs/peel-thunder-145-LCdnaJ.png"},
            "East Fremantle": {
                "logo_url": f"{base}/clubs/east-fremantle-146-LAbcde.png"
            },
            "Perth": {"logo_url": f"{base}/clubs/perth-147-KXyzuv.png"},
            "West Coast": {"logo_url": f"{base}/clubs/west-coast-148-MQrstu.png"},
        }

    def test_request_count_stays_within_club_count(self):
        """One discovery call, then one call per DISTINCT club — the
        fixture's six side slots collapse to five clubs (Peel appears
        twice and is fetched once), and clubs/{id} is hit ≤ that."""
        calls = []
        _run(self._provider(calls).get_team_metadata(2026))

        club_calls = [p for p, _ in calls if p.startswith("clubs/")]
        assert len(club_calls) == 5  # 5 distinct clubs, no dupes
        assert len(set(club_calls)) == len(club_calls)

    def test_other_competitions_in_payload_are_ignored(self):
        calls = []
        metadata = _run(self._provider(calls).get_team_metadata(2026))
        assert "Not Our Competition FC" not in metadata

    def test_club_fetch_failure_is_skipped_not_fatal(self):
        async def broken_club_fetch(path, params=None):
            if path.startswith("clubs/"):
                raise RuntimeError("edge 522")
            with open(_TRIMMED_FIXTURE, encoding="utf-8") as f:
                return json.load(f)

        provider = SportixProvider(
            source="sportix-wafl",
            competition_name="League",
            fetch_json=broken_club_fetch,
        )
        assert _run(provider.get_team_metadata(2026)) == {}


def _run(coro):
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
