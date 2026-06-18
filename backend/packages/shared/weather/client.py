"""Client for Open-Meteo weather API.

Fetches historical and forecast weather data for AFL venues.
Follows the same patterns as SquiggleClient (httpx.AsyncClient + Redis cache).
"""

from datetime import date
from typing import Any, Dict, Optional

import httpx

from ..cache import medium_cache
from ..logger import get_logger

logger = get_logger(__name__)

# Open-Meteo API endpoints
ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Hourly variables to request from Open-Meteo
HOURLY_VARS = (
    "temperature_2m,"
    "precipitation,"
    "windspeed_10m,"
    "winddirection_10m,"
    "windgusts_10m,"
    "relative_humidity_2m,"
    "weathercode"
)


class WeatherClient:
    """Client for Open-Meteo weather API — follows SquiggleClient pattern."""

    # Canonical AFL venue coordinates
    VENUE_COORDS: Dict[str, Dict[str, float]] = {
        "MCG": {"lat": -37.820, "lon": 144.984},
        "Marvel Stadium": {"lat": -37.817, "lon": 144.947},
        "Adelaide Oval": {"lat": -34.915, "lon": 138.596},
        "Optus Stadium": {"lat": -31.951, "lon": 115.889},
        "Gabba": {"lat": -27.486, "lon": 153.038},
        "SCG": {"lat": -33.891, "lon": 151.225},
        "GMHBA Stadium": {"lat": -38.157, "lon": 144.355},
        "People First Stadium": {"lat": -28.005, "lon": 153.426},
        "UTAS Stadium": {"lat": -42.834, "lon": 147.271},
        "Manuka Oval": {"lat": -35.322, "lon": 149.131},
    }

    # Alternate names → canonical names
    VENUE_ALIASES: Dict[str, str] = {
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

    def __init__(self) -> None:
        # SEC-LO-007: explicit `verify=True` so a future change to
        # httpx's default (or a deployment env that strips the CA
        # bundle) cannot silently disable TLS verification.
        self.client = httpx.AsyncClient(timeout=30.0, verify=True)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()

    async def __aenter__(self) -> "WeatherClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _get_venue_coords(self, venue: str) -> Optional[Dict[str, float]]:
        """Resolve a venue name (including aliases) to coordinates.

        Args:
            venue: Venue name (canonical or alias)

        Returns:
            Dict with 'lat' and 'lon' keys, or None if unknown.
        """
        # Resolve alias → canonical name
        canonical = self.VENUE_ALIASES.get(venue, venue)
        return self.VENUE_COORDS.get(canonical)

    async def get_match_day_weather(
        self,
        venue: str,
        match_date: date,
        match_hour_utc: int = 6,
    ) -> Dict[str, Any]:
        """Fetch historical weather for a match day.

        Uses the Open-Meteo archive API. Results are cached with a key
        derived from venue and date.

        Args:
            venue: Venue name (canonical or alias)
            match_date: Date of the match
            match_hour_utc: Approximate kickoff hour in UTC (default 6)

        Returns:
            Dict with venue, date, and hourly match-window data, or empty dict.
        """
        coords = self._get_venue_coords(venue)
        if coords is None:
            logger.warning(f"Unknown venue: {venue}")
            return {}

        date_str = match_date.isoformat()
        cache_key = f"weather:historical:{venue}:{date_str}"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for weather: {cache_key}")
            return cached

        # Build Open-Meteo archive request
        params = {
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "start_date": date_str,
            "end_date": date_str,
            "hourly": HOURLY_VARS,
            "timezone": "auto",
        }

        try:
            response = await self.client.get(ARCHIVE_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Open-Meteo archive API error: {e}")
            return {}

        # Extract match window and build result
        window = self._extract_match_window(data, match_hour_utc)
        result = {
            "venue": venue,
            "date": date_str,
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "hourly": window,
        }

        # Cache the result (medium_cache = 5 min TTL)
        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for weather: {cache_key}")

        return result

    async def get_forecast(
        self,
        venue: str,
        days: int = 7,
    ) -> Dict[str, Any]:
        """Fetch weather forecast for a venue.

        Uses the Open-Meteo forecast API.

        Args:
            venue: Venue name (canonical or alias)
            days: Number of forecast days (default 7)

        Returns:
            Dict with venue, forecast hourly data, or empty dict.
        """
        coords = self._get_venue_coords(venue)
        if coords is None:
            logger.warning(f"Unknown venue for forecast: {venue}")
            return {}

        # Check cache first
        cache_key = f"weather:forecast:{venue}:{days}"
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for forecast: {cache_key}")
            return cached

        params = {
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "hourly": HOURLY_VARS,
            "forecast_days": days,
            "timezone": "auto",
        }

        try:
            response = await self.client.get(FORECAST_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Open-Meteo forecast API error: {e}")
            return {}

        result = {
            "venue": venue,
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "hourly": data.get("hourly", {}),
        }

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for forecast: {cache_key}")

        return result

    def _extract_match_window(
        self,
        data: Dict[str, Any],
        match_hour_utc: int,
        window_hours: int = 2,
    ) -> Dict[str, Any]:
        """Extract hourly data for a match window (±window_hours around kickoff).

        Args:
            data: Open-Meteo response with hourly key
            match_hour_utc: Kickoff hour in UTC
            window_hours: Hours before/after kickoff to include

        Returns:
            Dict of hourly arrays limited to the match window.
        """
        hourly = data.get("hourly", {})
        if not hourly:
            return {}

        times = hourly.get("time", [])
        start_idx = max(0, match_hour_utc - window_hours)
        end_idx = min(len(times), match_hour_utc + window_hours + 1)

        result: Dict[str, Any] = {}
        for key, values in hourly.items():
            if isinstance(values, list):
                result[key] = values[start_idx:end_idx]

        return result
