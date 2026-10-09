"""NrlProvider — the FixtureDownload FeedProvider for rugby-league (Phase 5.2).

Every payload used here is a RECORDED fixture (``tests/fixtures/nrl/``)
faithful to the live-verified FixtureDownload JSON schema (verified
2026-10-09, see ``.tmp/external-context/nrl-feed/fixturedownload-feed.md``):

    {"MatchNumber": 1, "RoundNumber": 1, "DateUtc": "2026-03-01 02:15:00Z",
     "Location": "Allegiant Stadium", "HomeTeam": "Knights",
     "AwayTeam": "Cowboys", "Group": null, "HomeTeamScore": 28,
     "AwayTeamScore": 18, "Winner": "Knights"}

No test in this module performs live HTTP — the fetch is injected.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from packages.shared.ingestion.base import FeedProvider
from packages.shared.ingestion.nrl_provider import (
    NrlProvider,
    fixture_from_fixturedownload,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nrl"
_BASE_URL = "https://fixturedownload.com/feed/json"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _make_provider(
    competition: str = "nrl",
    payloads: dict | None = None,
    *,
    calls: list | None = None,
    clock=None,
    ttl_seconds: float | None = None,
    cache: dict | None = None,
) -> tuple[NrlProvider, list]:
    """Provider with an injected fetcher serving recorded payloads; the
    returned list records every fetched URL (fetch-count assertions)."""
    payloads = payloads if payloads is not None else {"nrl-2026": _load("nrl-2026.json")}
    calls = calls if calls is not None else []

    async def fetch_json(url: str):
        calls.append(url)
        slug = url.rsplit("/", 1)[-1]
        if slug not in payloads:
            raise FileNotFoundError(f"no recorded payload for {slug!r}")
        return payloads[slug]

    kwargs: dict = {"fetch_json": fetch_json}
    # Each provider gets its OWN cache unless sharing is the thing under
    # test — otherwise the module-level shared cache would leak state
    # between tests and break fetch-count assertions.
    kwargs["cache"] = cache if cache is not None else {}
    if clock is not None:
        kwargs["clock"] = clock
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    return NrlProvider(competition=competition, **kwargs), calls


class TestProtocolConformance:
    def test_satisfies_the_feedprovider_protocol(self):
        assert isinstance(NrlProvider(), FeedProvider)

    def test_sport_id_is_rugby_league(self):
        assert NrlProvider.sport_id == "rugby-league"
        assert NrlProvider().sport_id == "rugby-league"

    def test_source_names_the_platform_and_competition(self):
        assert NrlProvider().source == "fixturedownload-nrl"
        assert NrlProvider(competition="nrlw").source == "fixturedownload-nrlw"
        assert NrlProvider(competition="origin").source == "fixturedownload-origin"


class TestFeedSlugResolution:
    @pytest.mark.parametrize(
        ("competition", "season", "slug"),
        [
            ("nrl", 2026, "nrl-2026"),
            ("nrlw", 2026, "nrlw-2026"),
            ("origin", 2026, "state-of-origin-2026"),
        ],
    )
    async def test_slugs_resolve_per_competition_and_season(
        self, competition, season, slug
    ):
        payloads = {slug: _load(f"{slug}.json")}
        provider, calls = _make_provider(competition, payloads)

        await provider.get_fixtures(season)
        assert calls == [f"{_BASE_URL}/{slug}"]

    async def test_slug_year_interpolation_for_older_seasons(self):
        provider, calls = _make_provider("nrl", payloads={"nrl-2017": []})
        assert await provider.get_fixtures(2017) == []
        assert calls == [f"{_BASE_URL}/nrl-2017"]

    def test_unknown_competition_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="super-league"):
            NrlProvider(competition="super-league")


class TestVerifiedSchemaMapping:
    async def test_verified_round_1_record_maps_to_canonical_dto(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)
        match = next(f for f in fixtures if f.external_id == 1)

        assert match.source == "fixturedownload-nrl"
        assert match.season == 2026
        assert match.round_id == 1
        assert match.home_participant == "Knights"
        assert match.away_participant == "Cowboys"
        assert match.home_score == 28
        assert match.away_score == 18
        assert match.completed is True
        # Match 1 was the Las Vegas opener — an unknown venue, verbatim.
        assert match.venue == "Allegiant Stadium"

    async def test_dateutc_maps_to_tz_aware_utc_starts_at(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        match = next(f for f in fixtures if f.external_id == 1)
        assert match.starts_at == datetime(2026, 3, 1, 2, 15, tzinfo=timezone.utc)
        assert match.starts_at is not None
        assert match.starts_at.utcoffset() == timedelta(0)

    async def test_null_scores_mean_not_completed(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        upcoming = next(f for f in fixtures if f.external_id == 3)
        assert upcoming.home_score is None
        assert upcoming.away_score is None
        assert upcoming.completed is False
        # The kickoff time is still known pre-match.
        assert upcoming.starts_at == datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)

    async def test_draw_is_completed_even_though_winner_is_null(self):
        """Rugby league has draws: a level full-time score leaves
        ``Winner`` null, but the game IS complete.  Completion is driven
        by score presence, never by ``Winner``."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        draw = next(f for f in fixtures if f.external_id == 4)
        assert draw.home_score == 20
        assert draw.away_score == 20
        assert draw.completed is True


class TestPureMapper:
    def test_missing_dateutc_yields_none_starts_at(self):
        fixture = fixture_from_fixturedownload(
            {
                "MatchNumber": 9,
                "RoundNumber": 1,
                "DateUtc": None,
                "Location": None,
                "HomeTeam": None,
                "AwayTeam": None,
                "HomeTeamScore": None,
                "AwayTeamScore": None,
            },
            season=2026,
            source="fixturedownload-nrl",
        )
        assert fixture.external_id == 9
        assert fixture.starts_at is None
        assert fixture.venue is None
        assert fixture.home_participant is None
        assert fixture.away_participant is None
        assert fixture.completed is False

    def test_naive_dateutc_is_assumed_utc(self):
        fixture = fixture_from_fixturedownload(
            {"MatchNumber": 1, "DateUtc": "2026-03-01 02:15:00"},
            season=2026,
            source="fixturedownload-nrl",
        )
        assert fixture.starts_at == datetime(2026, 3, 1, 2, 15, tzinfo=timezone.utc)


class TestVenueCanonicalization:
    async def test_sponsor_branded_venues_resolve_to_canonical_grounds(self):
        provider, _ = _make_provider("nrl", {"nrl-2022": _load("nrl-2022.json")})
        fixtures = await provider.get_fixtures(2022)

        by_id = {f.external_id: f for f in fixtures}
        assert by_id[1].venue == "Shark Park"  # PointsBet Stadium
        assert by_id[2].venue == "Mount Smart Stadium"  # Mt Smart Stadium
        assert by_id[3].venue == "Newcastle Stadium"  # McDonalds Park
        assert by_id[4].venue == "Jubilee Stadium"  # Netstrata Jubilee

    async def test_same_ground_canonicalizes_across_seasons(self):
        """PointsBet (2022) and Ocean Protect (2026) are one ground."""
        payloads = {
            "nrl-2022": _load("nrl-2022.json"),
            "nrl-2026": _load("nrl-2026.json"),
        }
        provider, _ = _make_provider("nrl", payloads)

        fixtures = await provider.get_fixtures(2022) + await provider.get_fixtures(2026)
        shark_park_seasons = {
            f.season for f in fixtures if f.venue == "Shark Park"
        }
        assert shark_park_seasons == {2022, 2026}


class TestSeasonFilters:
    async def test_round_filter_keeps_only_that_round(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026, round=2)
        assert {f.external_id for f in fixtures} == {3, 4}

    async def test_complete_filter_true_keeps_played_matches(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026, complete=True)
        assert {f.external_id for f in fixtures} == {1, 2, 4, 5}

    async def test_complete_filter_false_keeps_unplayed_matches(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026, complete=False)
        assert [f.external_id for f in fixtures] == [3]

    async def test_date_window_is_inclusive_on_both_ends(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(
            2026, start_date="2026-03-08", end_date="2026-03-14"
        )
        assert {f.external_id for f in fixtures} == {3, 4}

    async def test_filters_combine(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026, complete=True, round=1)
        assert {f.external_id for f in fixtures} == {1, 2}


class TestPolitenessCaching:
    async def test_repeat_season_fetch_hits_the_cache(self):
        provider, calls = _make_provider()
        await provider.get_fixtures(2026)
        await provider.get_fixtures(2026)
        assert len(calls) == 1

    async def test_distinct_seasons_fetch_separately(self):
        payloads = {
            "nrl-2022": _load("nrl-2022.json"),
            "nrl-2026": _load("nrl-2026.json"),
        }
        provider, calls = _make_provider("nrl", payloads)
        await provider.get_fixtures(2022)
        await provider.get_fixtures(2026)
        assert len(calls) == 2

    async def test_expired_ttl_refetches(self):
        now = [1000.0]

        def clock() -> float:
            return now[0]

        provider, calls = _make_provider(ttl_seconds=86400.0, clock=clock)
        await provider.get_fixtures(2026)

        now[0] += 86399  # one second inside the TTL → cached
        await provider.get_fixtures(2026)
        assert len(calls) == 1

        now[0] += 2  # TTL expired → refetch
        await provider.get_fixtures(2026)
        assert len(calls) == 2

    async def test_cache_is_shared_across_instances_of_one_process(self):
        cache: dict = {}
        first, first_calls = _make_provider(cache=cache)
        second, second_calls = _make_provider(cache=cache)

        await first.get_fixtures(2026)
        await second.get_fixtures(2026)

        assert len(first_calls) == 1
        assert len(second_calls) == 0

    async def test_default_ttl_is_one_day(self):
        assert NrlProvider().ttl_seconds == 86400.0


class TestSingleFixtureLookup:
    async def test_lookup_after_season_fetch(self):
        provider, _ = _make_provider()
        await provider.get_fixtures(2026)

        draw = await provider.get_fixture(4)
        assert draw is not None
        assert draw.home_score == 20
        assert draw.away_score == 20

    async def test_lookup_miss_returns_none(self):
        provider, _ = _make_provider()
        await provider.get_fixtures(2026)
        assert await provider.get_fixture(999) is None

    async def test_lookup_with_cold_cache_returns_none_without_http(self):
        provider, calls = _make_provider()
        assert await provider.get_fixture(1) is None
        assert calls == []


class TestRobustness:
    async def test_fetch_failure_propagates(self):
        async def broken_fetch(url: str):
            raise RuntimeError("fixturedownload unreachable")

        provider = NrlProvider(fetch_json=broken_fetch)
        with pytest.raises(RuntimeError, match="unreachable"):
            await provider.get_fixtures(2026)

    async def test_non_list_payload_is_rejected(self):
        provider, _ = _make_provider(payloads={"nrl-2026": {"error": "nope"}})
        with pytest.raises(ValueError, match="payload"):
            await provider.get_fixtures(2026)


class TestPolitenessHeaders:
    def test_default_request_headers_carry_no_auth(self):
        """The feed needs no key; we identify our bot only."""
        headers = NrlProvider()._headers()
        assert "WhatIsMyTip" in headers["User-Agent"]
        assert headers["Accept"] == "application/json"
        assert "Authorization" not in headers
