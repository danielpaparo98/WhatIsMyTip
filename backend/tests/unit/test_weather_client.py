"""Unit tests for WeatherClient (Open-Meteo API).

Tests mock HTTP responses and verify parsing, caching, venue resolution,
and match-window extraction. No real HTTP requests are made.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.weather.client import WeatherClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hourly_response(
    latitude: float = -37.82,
    longitude: float = 144.984,
    date_str: str = "2025-03-21",
    hours: int = 24,
    temp_base: float = 15.0,
    precip_base: float = 0.0,
    wind_base: float = 12.0,
) -> dict:
    """Build a realistic Open-Meteo hourly response dict."""
    times = [f"{date_str}T{h:02d}:00" for h in range(hours)]
    temps = [round(temp_base + h * 0.1, 1) for h in range(hours)]
    precips = [round(precip_base + h * 0.05, 1) for h in range(hours)]
    winds = [round(wind_base + h * 0.2, 1) for h in range(hours)]
    dirs = [180 + h for h in range(hours)]
    gusts = [round(wind_base + 8 + h * 0.3, 1) for h in range(hours)]
    humidity = [65 + h for h in range(hours)]
    codes = [3 for _ in range(hours)]

    return {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "precipitation": precips,
            "windspeed_10m": winds,
            "winddirection_10m": dirs,
            "windgusts_10m": gusts,
            "relative_humidity_2m": humidity,
            "weathercode": codes,
        },
    }


# ---------------------------------------------------------------------------
# Venue coordinate tests
# ---------------------------------------------------------------------------

class TestVenueCoords:
    """Verify AFL venue coordinate definitions."""

    EXPECTED_VENUES = {
        "MCG": (-37.820, 144.984),
        "Marvel Stadium": (-37.817, 144.947),
        "Adelaide Oval": (-34.915, 138.596),
        "Optus Stadium": (-31.951, 115.889),
        "Gabba": (-27.486, 153.038),
        "SCG": (-33.891, 151.225),
        "GMHBA Stadium": (-38.157, 144.355),
        "People First Stadium": (-28.005, 153.426),
        "UTAS Stadium": (-42.834, 147.271),
        "Manuka Oval": (-35.322, 149.131),
    }

    def test_venue_coords_known_venues(self):
        """All 10 AFL venues must be defined with correct lat/lon."""
        for name, (lat, lon) in self.EXPECTED_VENUES.items():
            assert name in WeatherClient.VENUE_COORDS, f"Missing venue: {name}"
            coords = WeatherClient.VENUE_COORDS[name]
            assert coords["lat"] == lat, f"{name} lat mismatch"
            assert coords["lon"] == lon, f"{name} lon mismatch"


# ---------------------------------------------------------------------------
# Venue alias tests
# ---------------------------------------------------------------------------

class TestVenueAliases:
    """Verify venue alias resolution."""

    ALIAS_CASES = {
        "Docklands Stadium": "Marvel Stadium",
        "Etihad Stadium": "Marvel Stadium",
        "Perth Stadium": "Optus Stadium",
        "Metricon Stadium": "People First Stadium",
        "Carrara": "People First Stadium",
        "Kardinia Park": "GMHBA Stadium",
        "Skoda Stadium": "GMHBA Stadium",
        "York Park": "UTAS Stadium",
        "Aurora Stadium": "UTAS Stadium",
    }

    def test_venue_aliases_resolve_correctly(self):
        """Each alias should map to its canonical venue name."""
        WeatherClient.__new__(WeatherClient)  # skip __init__
        for alias, expected in self.ALIAS_CASES.items():
            assert WeatherClient.VENUE_ALIASES.get(alias) == expected, (
                f"Alias '{alias}' should resolve to '{expected}'"
            )

    def test_get_venue_coords_unknown_returns_none(self):
        """Unknown venue name should return None."""
        client = WeatherClient.__new__(WeatherClient)
        result = client._get_venue_coords("Totally Unknown Stadium")
        assert result is None

    def test_get_venue_coords_alias_resolves(self):
        """Aliased venue should resolve via canonical name to coords."""
        client = WeatherClient.__new__(WeatherClient)
        result = client._get_venue_coords("Etihad Stadium")
        assert result is not None
        assert result["lat"] == -37.817
        assert result["lon"] == 144.947

    def test_get_venue_coords_canonical(self):
        """Canonical venue name should return correct coords."""
        client = WeatherClient.__new__(WeatherClient)
        result = client._get_venue_coords("MCG")
        assert result is not None
        assert result["lat"] == -37.820
        assert result["lon"] == 144.984


# ---------------------------------------------------------------------------
# get_match_day_weather tests
# ---------------------------------------------------------------------------

class TestGetMatchDayWeather:
    """Tests for historical weather fetching."""

    @pytest.mark.asyncio
    async def test_get_match_day_weather_success(self):
        """Successful API response should be parsed correctly."""
        api_response = _make_hourly_response()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = api_response

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.weather.client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = WeatherClient.__new__(WeatherClient)
            client.client = mock_http_client

            result = await client.get_match_day_weather(
                "MCG", date(2025, 3, 21), match_hour_utc=6
            )

        assert "venue" in result
        assert result["venue"] == "MCG"
        assert result["date"] == "2025-03-21"
        assert "hourly" in result
        # Match window should be subset of hours (±2 around hour 6 → 4..8)
        assert len(result["hourly"]["time"]) == 5  # hours 4,5,6,7,8

    @pytest.mark.asyncio
    async def test_get_match_day_weather_unknown_venue(self):
        """Unknown venue should return empty dict."""
        client = WeatherClient.__new__(WeatherClient)
        result = await client.get_match_day_weather(
            "Nowhere Stadium", date(2025, 3, 21)
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_match_day_weather_caches_result(self):
        """Result should be stored in cache."""
        api_response = _make_hourly_response()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = api_response

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with patch("packages.shared.weather.client.medium_cache", mock_cache):
            client = WeatherClient.__new__(WeatherClient)
            client.client = mock_http_client

            await client.get_match_day_weather(
                "MCG", date(2025, 3, 21), match_hour_utc=6
            )

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        cache_key = call_args[0][0]
        assert "weather:historical" in cache_key
        assert "MCG" in cache_key
        assert "2025-03-21" in cache_key

    @pytest.mark.asyncio
    async def test_get_match_day_weather_returns_cached(self):
        """Cached result should be returned without HTTP call."""
        cached_data = {"venue": "MCG", "date": "2025-03-21", "hourly": {}}

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=cached_data)
        mock_cache.set = AsyncMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock()

        with patch("packages.shared.weather.client.medium_cache", mock_cache):
            client = WeatherClient.__new__(WeatherClient)
            client.client = mock_http_client

            result = await client.get_match_day_weather(
                "MCG", date(2025, 3, 21)
            )

        assert result == cached_data
        mock_http_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_match_day_weather_api_error(self):
        """HTTP error should be caught and return empty dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("HTTP 500")
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.weather.client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = WeatherClient.__new__(WeatherClient)
            client.client = mock_http_client

            result = await client.get_match_day_weather(
                "MCG", date(2025, 3, 21)
            )

        assert result == {}


# ---------------------------------------------------------------------------
# get_forecast tests
# ---------------------------------------------------------------------------

class TestGetForecast:
    """Tests for forecast weather fetching."""

    @pytest.mark.asyncio
    async def test_get_forecast_success(self):
        """Successful forecast response should be parsed correctly."""
        api_response = _make_hourly_response()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = api_response

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.weather.client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = WeatherClient.__new__(WeatherClient)
            client.client = mock_http_client

            result = await client.get_forecast("MCG", days=7)

        assert "venue" in result
        assert result["venue"] == "MCG"
        assert "hourly" in result
        assert "latitude" in result

    @pytest.mark.asyncio
    async def test_get_forecast_unknown_venue(self):
        """Unknown venue should return empty dict."""
        client = WeatherClient.__new__(WeatherClient)
        result = await client.get_forecast("Imaginary Ground")
        assert result == {}


# ---------------------------------------------------------------------------
# _extract_match_window tests
# ---------------------------------------------------------------------------

class TestExtractMatchWindow:
    """Tests for match window extraction logic."""

    def test_extract_match_window(self):
        """Should return ±2 hours around kickoff (5 total hours)."""
        client = WeatherClient.__new__(WeatherClient)

        data = _make_hourly_response(date_str="2025-03-21", hours=24)

        result = client._extract_match_window(data, match_hour_utc=6, window_hours=2)

        # Hours 4, 5, 6, 7, 8 → 5 entries
        times = result["time"]
        assert len(times) == 5
        assert times[0] == "2025-03-21T04:00"
        assert times[2] == "2025-03-21T06:00"
        assert times[4] == "2025-03-21T08:00"

        # All other fields should also have 5 entries
        assert len(result["temperature_2m"]) == 5
        assert len(result["precipitation"]) == 5
        assert len(result["windspeed_10m"]) == 5

    def test_extract_match_window_custom_window(self):
        """Custom window of ±1 hour should give 3 entries."""
        client = WeatherClient.__new__(WeatherClient)

        data = _make_hourly_response(date_str="2025-06-15", hours=24)

        result = client._extract_match_window(data, match_hour_utc=12, window_hours=1)

        times = result["time"]
        assert len(times) == 3
        assert times[0] == "2025-06-15T11:00"
        assert times[1] == "2025-06-15T12:00"
        assert times[2] == "2025-06-15T13:00"

    def test_extract_match_window_edge_start(self):
        """Match hour at 0 should clamp to available data."""
        client = WeatherClient.__new__(WeatherClient)

        data = _make_hourly_response(date_str="2025-03-21", hours=24)

        result = client._extract_match_window(data, match_hour_utc=0, window_hours=2)

        times = result["time"]
        # Start clamped to 0, so hours 0,1,2 → 3 entries
        assert len(times) == 3
        assert times[0] == "2025-03-21T00:00"

    def test_extract_match_window_edge_end(self):
        """Match hour near end of data should clamp."""
        client = WeatherClient.__new__(WeatherClient)

        data = _make_hourly_response(date_str="2025-03-21", hours=24)

        result = client._extract_match_window(data, match_hour_utc=23, window_hours=2)

        times = result["time"]
        # End clamped to 23, so hours 21,22,23 → 3 entries
        assert len(times) == 3
        assert times[-1] == "2025-03-21T23:00"


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------

class TestContextManager:
    """Tests for async context manager protocol."""

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """__aenter__ should return self; __aexit__ should call close()."""
        client = WeatherClient()
        assert isinstance(client.client, type(client.client))  # httpx.AsyncClient

        entered = await client.__aenter__()
        assert entered is client

        # Mock aclose so we don't actually close a real client
        client.client.aclose = AsyncMock()
        await client.__aexit__(None, None, None)
        client.client.aclose.assert_called_once()
