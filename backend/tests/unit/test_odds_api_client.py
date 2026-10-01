"""Unit tests for The Odds API client parsing (BT-ODDS).

The client fetches AFL head-to-head decimal odds and normalises each
event to a median price per side across the AU bookmakers returned.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from packages.shared.odds_api.client import OddsAPIClient, parse_event


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


class TestClientConfig:
    def test_client_reads_sport_config_from_settings(self):
        """The client reads base/sport/regions from settings."""
        from packages.shared.config import settings

        client = OddsAPIClient()
        assert client.base_url == settings.odds_api_base
        assert client.sport_key == settings.odds_api_sport_key
        assert client.regions == settings.odds_api_regions
