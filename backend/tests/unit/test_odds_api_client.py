"""Unit tests for The Odds API client parsing (BT-ODDS).

The client fetches AFL head-to-head decimal odds and normalises each
event to a median price per side across the AU bookmakers returned.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from packages.shared.odds_api.client import (
    OddsAPIClient,
    odds_api_canonical_team,
    parse_event,
)


def _event(bookmakers: list[dict], **overrides) -> dict:
    base = {
        "id": "abc123",
        "commence_time": "2026-04-04T05:20:00Z",
        "home_team": "Sydney Swans",
        "away_team": "West Coast Eagles",
        "bookmakers": bookmakers,
    }
    base.update(overrides)
    return base


def _book(key: str, home_price: float, away_price: float) -> dict:
    return {
        "key": key,
        "title": key.title(),
        "last_update": "2026-04-03T02:00:00Z",
        "markets": [
            {
                "key": "h2h",
                "outcomes": [
                    {"name": "Sydney Swans", "price": home_price},
                    {"name": "West Coast Eagles", "price": away_price},
                ],
            }
        ],
    }


class TestParseEvent:
    def test_median_price_across_bookmakers(self):
        """Consensus price = median across books (robust to outliers)."""
        event = _event(
            [
                _book("sportsbet", 1.50, 2.60),
                _book("tab", 1.55, 2.50),
                _book("neds", 1.20, 5.00),  # outlier
            ]
        )
        parsed = parse_event(event)
        assert parsed["home_odds"] == pytest.approx(1.50)
        assert parsed["away_odds"] == pytest.approx(2.60)
        # Bookmaker label records the consensus breadth
        assert "3" in parsed["bookmaker"]

    def test_even_book_count_averages_middle_two(self):
        event = _event(
            [
                _book("sportsbet", 1.40, 2.90),
                _book("tab", 1.60, 2.40),
            ]
        )
        parsed = parse_event(event)
        assert parsed["home_odds"] == pytest.approx(1.5)
        assert parsed["away_odds"] == pytest.approx(2.65)

    def test_missing_side_price_is_none(self):
        """A book quoting only one side is skipped for the missing side."""
        event = _event(
            [
                {
                    "key": "tab",
                    "title": "TAB",
                    "last_update": "2026-04-03T02:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [{"name": "Sydney Swans", "price": 1.52}],
                        }
                    ],
                }
            ]
        )
        parsed = parse_event(event)
        assert parsed["home_odds"] == pytest.approx(1.52)
        assert parsed["away_odds"] is None

    def test_corrupt_prices_dropped(self):
        """Prices ≤ 1.0 are impossible decimal odds — dropped, not settled."""
        event = _event(
            [
                _book("sportsbet", 0.95, 2.60),
                _book("tab", 1.55, 2.50),
            ]
        )
        parsed = parse_event(event)
        assert parsed["home_odds"] == pytest.approx(1.55)
        assert parsed["away_odds"] == pytest.approx(2.55)

    def test_unknown_team_names_normalised_via_canonical_map(self):
        """Odds API names like 'GWS Giants' must match our canonical
        'Giants' so the sync can join them to games."""
        event = _event(
            [_book("sportsbet", 1.90, 1.92)],
            home_team="GWS Giants",
            away_team="North Melbourne Kangaroos",
        )
        parsed = parse_event(event)
        assert parsed["home_team"] == "Giants"
        assert parsed["away_team"] == "NorthMelbourne"

    def test_commence_time_parsed_as_utc(self):
        parsed = parse_event(_event([_book("tab", 1.5, 2.5)]))
        assert parsed["commence_time"] == datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)

    def test_no_bookmakers_yields_none_prices(self):
        parsed = parse_event(_event([]))
        assert parsed["home_odds"] is None
        assert parsed["away_odds"] is None


class TestVendorTeamAliases:
    """m-5: every AFL club must resolve from the names The Odds API
    actually publishes — a silent gap becomes a permanently unmatched
    event and missing odds for that club."""

    # The 18 clubs as The Odds API names them (full marketing names for
    # most clubs).
    ODDS_API_AFL_CLUBS = [
        "Adelaide Crows",
        "Brisbane Lions",
        "Carlton Blues",
        "Collingwood Magpies",
        "Essendon Bombers",
        "Fremantle Dockers",
        "Geelong Cats",
        "Gold Coast Suns",
        "GWS Giants",
        "Greater Western Sydney Giants",
        "Hawthorn Hawks",
        "Melbourne Demons",
        "North Melbourne Kangaroos",
        "Port Adelaide Power",
        "Richmond Tigers",
        "St Kilda Saints",
        "Sydney Swans",
        "West Coast Eagles",
        "Western Bulldogs",
    ]

    EXPECTED_CANONICAL = {
        "Adelaide",
        "Brisbane",
        "Carlton",
        "Collingwood",
        "Essendon",
        "Fremantle",
        "Geelong",
        "GoldCoast",
        "Giants",
        "Hawthorn",
        "Melbourne",
        "NorthMelbourne",
        "PortAdelaide",
        "Richmond",
        "StKilda",
        "Sydney",
        "WestCoast",
        "Bulldogs",
    }

    def test_all_18_clubs_resolve_through_vendor_aliases(self):
        resolved = {odds_api_canonical_team(name) for name in self.ODDS_API_AFL_CLUBS}
        missing = self.EXPECTED_CANONICAL - resolved
        assert not missing, f"clubs unresolvable from Odds API names: {sorted(missing)}"

    def test_short_alias_forms_still_resolve(self):
        """Squiggle-dialect names on the games side also resolve through
        the same normalisation path."""
        assert odds_api_canonical_team("Sydney") == "Sydney"
        assert odds_api_canonical_team("GWS") == "Giants"
        assert odds_api_canonical_team("Western Bulldogs") == "Bulldogs"


class TestHttpErrorRedaction:
    """M-1: BaseJob persists error strings to job_executions and alert
    webhooks — a failed request must never leak the API key."""

    @pytest.mark.asyncio
    async def test_http_error_message_is_redacted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = OddsAPIClient()
        # Inject the secret directly so the test doesn't touch settings.
        client.api_key = "super-secret-key"
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(httpx.HTTPStatusError) as excinfo:
                await client.get_afl_head_to_head_odds()

            message = str(excinfo.value)
            assert "super-secret-key" not in message
            assert "apiKey" not in message
            # The path survives so operators can see what failed.
            assert "/sports/aussie_rules_afl/odds" in message
        finally:
            await client.client.aclose()

    @pytest.mark.asyncio
    async def test_successful_fetch_parses_events(self):
        """Happy path: a list payload parses into normalised events."""

        def handler(request: httpx.Request) -> httpx.Response:
            # Echo the auth query param to prove it is being sent.
            assert "apiKey=" in str(request.url)
            return httpx.Response(
                200,
                json=[
                    _event(
                        [
                            _book("sportsbet", 1.50, 2.60),
                        ]
                    )
                ],
                headers={"x-requests-remaining": "480", "x-requests-used": "20"},
            )

        client = OddsAPIClient()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            events = await client.get_afl_head_to_head_odds()
        finally:
            await client.client.aclose()

        assert len(events) == 1
        assert events[0]["home_odds"] == pytest.approx(1.50)

    @pytest.mark.asyncio
    async def test_non_list_payload_returns_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "blocked"})

        client = OddsAPIClient()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            events = await client.get_afl_head_to_head_odds()
        finally:
            await client.client.aclose()

        assert events == []


class TestClientConfig:
    def test_client_reads_sport_config_from_settings(self):
        """The client reads base/sport/regions from settings."""
        from packages.shared.config import settings

        client = OddsAPIClient()
        assert client.base_url == settings.odds_api_base
        assert client.sport_key == settings.odds_api_sport_key
        assert client.regions == settings.odds_api_regions
