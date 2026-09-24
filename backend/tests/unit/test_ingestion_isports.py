"""ISportsProvider tests — driven by real iSports payloads captured
from stats.isports.net.au/api (QAFL league 1, 2026 season id 54):

* ``qafl_teams_2026.json``   — the complete real ``/seasons/54/teams``
  response (13 teams), verbatim.
* ``qafl_matches_2026.json`` — trimmed season slice: the real round-9
  match (id 2435, captured verbatim) plus shape-identical entries built
  on the real team/venue data to exercise every documented payload
  variant (reversed teamReports, absent location, absent nested team
  objects, empty/partial teamReports, non-PUBLISHED status).  The live
  API is unfilterable (~110-match full-season response), so the trim
  could not be done mechanically at capture time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from packages.shared.ingestion.isports_provider import ISportsProvider

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "isports")
_MATCHES_PATH = os.path.join(_FIXTURES, "qafl_matches_2026.json")
_TEAMS_PATH = os.path.join(_FIXTURES, "qafl_teams_2026.json")

# Real /leagues/1/seasons payload (id/name pairs; fetched 2026-09-24 —
# note the API returns seasons unordered).
_SEASONS = [
    {"id": 4, "name": "2023"},
    {"id": 34, "name": "2025"},
    {"id": 21, "name": "2024"},
    {"id": 54, "name": "2026"},
]


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_provider() -> tuple[ISportsProvider, list]:
    """Provider with a recording fake fetch wired to the real fixtures."""
    calls: list = []
    matches = _load(_MATCHES_PATH)
    teams = _load(_TEAMS_PATH)

    async def fake_fetch(path: str, params: dict | None = None):
        calls.append((path, params or {}))
        if path == "leagues/1/seasons":
            return _SEASONS
        if path == "seasons/54/matches":
            return matches
        if path == "seasons/54/teams":
            return teams
        raise AssertionError(f"unexpected call: {path} {params}")

    return ISportsProvider(league_id=1, fetch_json=fake_fetch), calls


class TestISportsQAFL:
    @pytest.mark.asyncio
    async def test_parses_the_trimmed_2026_season(self):
        provider, calls = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 14
        # Teams lookup happens alongside the matches fetch.
        assert any(c[0] == "leagues/1/seasons" for c in calls)
        assert any(c[0] == "seasons/54/matches" for c in calls)
        assert any(c[0] == "seasons/54/teams" for c in calls)

    @pytest.mark.asyncio
    async def test_real_round_9_match_maps_correctly(self):
        """Match 2435 was captured verbatim from the live API."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "2435")
        assert dto.source == "isports"
        assert dto.season == 2026
        assert dto.round_id == 9
        assert dto.home_participant == "Maroochydore Roos"
        assert dto.away_participant == "Labrador Tigers"
        assert dto.home_score == 122
        assert dto.away_score == 67
        assert dto.venue == "Maroochydore Multi Sports Complex"
        assert dto.starts_at == datetime(2026, 5, 30, 14, 0, tzinfo=timezone.utc)
        assert dto.completed is True

    @pytest.mark.asyncio
    async def test_scores_follow_team_id_not_report_order(self):
        """teamReports order is not guaranteed home-first — the real
        payload happens to list home first; this entry lists away first."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9100")
        assert dto.home_participant == "Aspley Hornets"
        assert dto.away_participant == "Morningside Panthers"
        assert dto.home_score == 78
        assert dto.away_score == 91
        assert dto.completed is True

    @pytest.mark.asyncio
    async def test_nested_team_objects_are_fallback_only(self):
        """Entry 9103 has no embedded homeTeam/awayTeam — names must
        resolve from the /teams id→name map."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9103")
        assert dto.home_participant == "Noosa Tigers"
        assert dto.away_participant == "Wilston Grange Gorillas"

    @pytest.mark.asyncio
    async def test_empty_team_reports_mean_no_score(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9104")
        assert dto.home_score is None
        assert dto.away_score is None
        assert dto.completed is False

    @pytest.mark.asyncio
    async def test_non_published_status_is_not_completed(self):
        """Entry 9106 carries scores but status 'DRAFT' — the status
        gate must keep completed False."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9106")
        assert dto.home_score == 45
        assert dto.away_score == 39
        assert dto.completed is False

    @pytest.mark.asyncio
    async def test_partial_team_reports_are_not_completed(self):
        """Entry 9107 has only the home team's report."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9107")
        assert dto.home_score == 90
        assert dto.away_score is None
        assert dto.completed is False

    @pytest.mark.asyncio
    async def test_missing_location_block_maps_venue_none(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "9101")
        assert dto.venue is None

    @pytest.mark.asyncio
    async def test_round_numbers_are_present(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        assert all(f.round_id is not None for f in fixtures)
        assert 1 in {f.round_id for f in fixtures}


class TestISportsPlumbing:
    def test_sport_and_source_identity(self):
        provider, _ = _make_provider()
        assert provider.sport_id == "afl"
        assert provider.source == "isports"

    @pytest.mark.asyncio
    async def test_get_seasons_returns_real_season_list(self):
        provider, _ = _make_provider()
        seasons = await provider.get_seasons()

        assert len(seasons) == 4
        assert {"id": 54, "name": "2026"} in seasons

    @pytest.mark.asyncio
    async def test_unknown_season_raises(self):
        provider, _ = _make_provider()
        with pytest.raises(ValueError, match="1999"):
            await provider.get_fixtures(1999)

    @pytest.mark.asyncio
    async def test_get_fixture_is_not_offered(self):
        provider, _ = _make_provider()
        with pytest.raises(NotImplementedError):
            await provider.get_fixture(2435)

    def test_default_headers_are_open_and_polite(self):
        provider = ISportsProvider(league_id=1)
        headers = provider._headers()
        assert "Authorization" not in headers
        assert "WhatIsMyTip" in headers["User-Agent"]
        assert headers["Accept"] == "application/json"
