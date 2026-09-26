"""AflPlatformProvider tests — driven by REAL AFL-platform match
responses captured from aflapi.afl.com.au/afl/v2 and stored as
fixtures, so parser tests don't touch the network:

* ``vfl_matches_2026.json``   — VFL 2026, complete season, 209 matches.
* ``aflw_matches_2026.json``  — AFLW 2026, in progress, 117 matches.

Expected field values are derived from the raw fixture payload at
runtime (the payload IS the truth), keeping these tests valid when the
fixture file is re-captured.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
import pytest

from packages.shared.ingestion.afl_platform_provider import AflPlatformProvider

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "afl_platform")
_VFL_PATH = os.path.join(_FIXTURES, "vfl_matches_2026.json")
_AFLW_PATH = os.path.join(_FIXTURES, "aflw_matches_2026.json")

# CompSeason ids verified live against aflapi compseasons (2026-09-24).
_SEASONS = {
    7: {
        "compSeasons": [
            {"id": 88, "name": "2026 VFL Premiership Season"},
            {"id": 77, "name": "2025 VFL Premiership Season"},
        ]
    },
    3: {
        "compSeasons": [
            {"id": 96, "name": "2026 NAB AFLW Season"},
            {"id": 84, "name": "2025 NAB AFLW Season"},
        ]
    },
}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_provider(
    competition_id: int,
    matches_payload: dict,
    single_match: dict | None = None,
    raise_on_single: Exception | None = None,
) -> tuple[AflPlatformProvider, list]:
    """Provider with a recording fake fetch wired to the real fixture."""
    calls: list = []

    async def fake_fetch(path: str, params: dict | None = None):
        params = params or {}
        calls.append((path, params))
        if path == f"competitions/{competition_id}/compseasons":
            return _SEASONS[competition_id]
        if path == "matches" and params.get("compSeasonId"):
            return matches_payload
        if path.startswith("matches/"):
            if raise_on_single is not None:
                raise raise_on_single
            return single_match
        raise AssertionError(f"unexpected call: {path} {params}")

    return (
        AflPlatformProvider(
            competition_id=competition_id,
            competition_name="Test Competition",
            fetch_json=fake_fetch,
        ),
        calls,
    )


def _dto_for(fixtures, raw_match) -> object:
    return next(f for f in fixtures if f.external_id == str(raw_match["id"]))


class TestAflPlatformVFL:
    @pytest.mark.asyncio
    async def test_parses_the_real_2026_season(self):
        provider, calls = _make_provider(7, _load(_VFL_PATH))
        fixtures = await provider.get_fixtures(2026)

        # The real 2026 VFL season payload carries 209 matches.
        assert len(fixtures) == 209
        # Season discovery + full fetch hit the documented endpoints.
        assert any(
            c[0] == "competitions/7/compseasons" and c[1] == {"pageSize": 50}
            for c in calls
        )
        assert any(
            c[0] == "matches"
            and c[1].get("competitionId") == 7
            and c[1].get("compSeasonId") == 88
            and c[1].get("pageSize") == 300
            and c[1].get("pageNum") == 1
            for c in calls
        )

    @pytest.mark.asyncio
    async def test_first_round_1_match_maps_correctly(self):
        payload = _load(_VFL_PATH)
        provider, _ = _make_provider(7, payload)
        fixtures = await provider.get_fixtures(2026)

        raw = next(
            m
            for m in payload["matches"]
            if (m.get("round") or {}).get("roundNumber") == 1
        )
        dto = _dto_for(fixtures, raw)

        assert dto.source == "aflapi"
        assert dto.season == 2026
        assert dto.external_id == str(raw["id"])
        assert dto.round_id == 1
        assert dto.home_participant == raw["home"]["team"]["name"]
        assert dto.away_participant == raw["away"]["team"]["name"]
        assert dto.venue == raw["venue"]["name"]
        expected_start = datetime.fromisoformat(
            raw["utcStartTime"].replace("+0000", "+00:00")
        )
        assert dto.starts_at == expected_start
        assert dto.starts_at.tzinfo is timezone.utc
        assert dto.completed == (raw["status"] == "CONCLUDED")
        if raw["status"] == "CONCLUDED":
            assert dto.home_score == raw["home"]["score"]["totalScore"]
            assert dto.away_score == raw["away"]["score"]["totalScore"]

    @pytest.mark.asyncio
    async def test_concluded_match_has_scores_and_completed(self):
        payload = _load(_VFL_PATH)
        provider, _ = _make_provider(7, payload)
        fixtures = await provider.get_fixtures(2026)

        raw = next(m for m in payload["matches"] if m["status"] == "CONCLUDED")
        dto = _dto_for(fixtures, raw)

        assert dto.completed is True
        assert dto.home_score == raw["home"]["score"]["totalScore"]
        assert dto.away_score == raw["away"]["score"]["totalScore"]
        assert isinstance(dto.home_score, int)
        assert isinstance(dto.away_score, int)

    @pytest.mark.asyncio
    async def test_round_numbers_are_present(self):
        provider, _ = _make_provider(7, _load(_VFL_PATH))
        fixtures = await provider.get_fixtures(2026)

        round_ids = {f.round_id for f in fixtures}
        assert all(r is not None for r in round_ids)
        assert 1 in round_ids
        # VFL 2026 ran deep into the year (finals included).
        assert max(round_ids) >= 20


class TestAflPlatformAFLW:
    @pytest.mark.asyncio
    async def test_parses_the_real_2026_season(self):
        provider, calls = _make_provider(3, _load(_AFLW_PATH))
        fixtures = await provider.get_fixtures(2026)

        # The real 2026 AFLW season payload (in progress) carries 117 matches.
        assert len(fixtures) == 117
        assert any(
            c[0] == "matches"
            and c[1].get("competitionId") == 3
            and c[1].get("compSeasonId") == 96
            for c in calls
        )

    @pytest.mark.asyncio
    async def test_scheduled_match_has_no_score_and_not_completed(self):
        """Season in progress: SCHEDULED/PLACEHOLDER matches carry no
        score blocks and must not be flagged complete."""
        payload = _load(_AFLW_PATH)
        provider, _ = _make_provider(3, payload)
        fixtures = await provider.get_fixtures(2026)

        raw = next(m for m in payload["matches"] if m["status"] != "CONCLUDED")
        dto = _dto_for(fixtures, raw)

        assert dto.completed is False
        assert dto.home_score is None
        assert dto.away_score is None

    @pytest.mark.asyncio
    async def test_round_numbers_are_present(self):
        provider, _ = _make_provider(3, _load(_AFLW_PATH))
        fixtures = await provider.get_fixtures(2026)

        round_ids = {f.round_id for f in fixtures}
        assert all(r is not None for r in round_ids)
        assert 1 in round_ids


class TestAflPlatformPlumbing:
    def test_sport_and_source_identity(self):
        provider, _ = _make_provider(7, {"matches": []})
        assert provider.sport_id == "afl"
        assert provider.source == "aflapi"

    @pytest.mark.asyncio
    async def test_season_resolution_matches_full_title(self):
        """compSeason names are full titles ('2026 VFL Premiership
        Season'), not bare years — resolution must still find 2026."""
        provider, _ = _make_provider(7, {"matches": []})
        assert await provider._resolve_season_id(2026) == 88

    @pytest.mark.asyncio
    async def test_unknown_season_raises(self):
        provider, _ = _make_provider(7, {"matches": []})
        with pytest.raises(ValueError, match="1999"):
            await provider._resolve_season_id(1999)

    @pytest.mark.asyncio
    async def test_get_fixture_maps_a_single_match(self):
        payload = _load(_VFL_PATH)
        raw = payload["matches"][0]
        provider, calls = _make_provider(7, payload, single_match=raw)

        dto = await provider.get_fixture(raw["id"])

        assert calls[-1] == (f"matches/{raw['id']}", {})
        assert dto is not None
        assert dto.external_id == str(raw["id"])
        assert dto.season == 2026  # derived from compSeason name prefix
        assert dto.home_participant == raw["home"]["team"]["name"]
        assert dto.completed == (raw["status"] == "CONCLUDED")

    @pytest.mark.asyncio
    async def test_get_fixture_returns_none_on_404(self):
        request = httpx.Request("GET", "https://aflapi.afl.com.au/afl/v2/matches/1")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError(
            "Not Found", request=request, response=response
        )
        provider, _ = _make_provider(7, {"matches": []}, raise_on_single=error)

        assert await provider.get_fixture(1) is None

    def test_default_headers_are_open_and_polite(self):
        provider = AflPlatformProvider(competition_id=7, competition_name="VFL")
        headers = provider._headers()
        assert "Authorization" not in headers
        assert "WhatIsMyTip" in headers["User-Agent"]
        assert headers["Accept"] == "application/json"
