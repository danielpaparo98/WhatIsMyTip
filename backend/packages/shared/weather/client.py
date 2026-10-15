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
GEOCODING_BASE_URL = "https://geocoding-api.open-meteo.com/v1/search"

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
        # GF-CONTENT FIX (2026-09-20): Squiggle feeds the dotted form —
        # without this the grand-final forecast lookup returned unknown
        # and the report's weather section went blank.
        "M.C.G.": "MCG",
    }

    # PARITY-1: league venues the AFL map does not know — the major
    # rugby-league and state-league grounds, keyed on the names the
    # feeds emit.  Anything not in either map falls through to the
    # Open-Meteo geocoding API (see ``resolve_coords``), so an unmapped
    # ground degrades gracefully instead of returning no weather.
    LEAGUE_VENUE_COORDS: Dict[str, Dict[str, float]] = {
        # --- NRL / rugby league -------------------------------------
        "Accor Stadium": {"lat": -33.888, "lon": 151.065},
        "Sydney Olympic Park": {"lat": -33.888, "lon": 151.065},
        "Allianz Stadium": {"lat": -33.892, "lon": 151.225},
        "Sydney Cricket Ground": {"lat": -33.891, "lon": 151.225},
        "Suncorp Stadium": {"lat": -27.464, "lon": 153.009},
        "Melbourne Cricket Ground": {"lat": -37.820, "lon": 144.984},
        "AAMI Park": {"lat": -37.821, "lon": 144.983},
        "Apollo Stadium": {"lat": -34.915, "lon": 138.596},
        "4 Pines Park": {"lat": -33.800, "lon": 151.287},
        "Brookvale Oval": {"lat": -33.800, "lon": 151.287},
        "BlueBet Stadium": {"lat": -33.783, "lon": 150.776},
        "Penrith Stadium": {"lat": -33.783, "lon": 150.776},
        "CommBank Stadium": {"lat": -33.813, "lon": 150.983},
        "Parramatta Stadium": {"lat": -33.813, "lon": 150.983},
        "PointsBet Stadium": {"lat": -33.956, "lon": 151.245},
        "Shark Park": {"lat": -33.956, "lon": 151.245},
        "CBus Super Stadium": {"lat": -27.967, "lon": 153.380},
        "Robina Stadium": {"lat": -27.967, "lon": 153.380},
        "Queensland Country Bank Stadium": {"lat": -19.264, "lon": 146.817},
        "BB Print Stadium": {"lat": -21.148, "lon": 149.178},
        "Browne Park": {"lat": -23.373, "lon": 150.511},
        "Kayo Stadium": {"lat": -27.340, "lon": 153.118},
        "Dolphin Stadium": {"lat": -27.340, "lon": 153.118},
        "McDonald Jones Stadium": {"lat": -32.918, "lon": 151.782},
        "Newcastle Stadium": {"lat": -32.918, "lon": 151.782},
        "Win Stadium": {"lat": -34.424, "lon": 150.893},
        "Orange Bassalt": {"lat": -34.424, "lon": 150.893},
        "Leichhardt Oval": {"lat": -33.858, "lon": 151.143},
        "Campbelltown Stadium": {"lat": -34.065, "lon": 150.814},
        "Netstrata Jubilee Stadium": {"lat": -33.955, "lon": 151.194},
        "Kogarah Oval": {"lat": -33.955, "lon": 151.194},
        "McDonalds Stadium": {"lat": -42.834, "lon": 147.271},
        "NIB Stadium": {"lat": -31.949, "lon": 115.889},
        "HBF Park": {"lat": -31.949, "lon": 115.889},
        # --- state-league football grounds --------------------------
        "Leederville Oval": {"lat": -31.920, "lon": 115.818},
        "Optus Oval": {"lat": -31.949, "lon": 115.877},
        "Joondalup Arena": {"lat": -31.745, "lon": 115.764},
        "Revo Fitness Stadium": {"lat": -31.949, "lon": 115.877},
        "Lane Group Stadium": {"lat": -32.050, "lon": 115.890},
        "Rushton Park": {"lat": -32.530, "lon": 115.740},
        "Sullivan Logistics Stadium": {"lat": -31.933, "lon": 115.890},
        "Adelaide Oval": {"lat": -34.915, "lon": 138.596},
        "Thebarton Oval": {"lat": -34.912, "lon": 138.573},
        "Glenelg Oval": {"lat": -34.972, "lon": 138.513},
        "Norwood Oval": {"lat": -34.936, "lon": 138.639},
        "Unley Oval": {"lat": -34.953, "lon": 138.608},
        "Prospect Oval": {"lat": -34.884, "lon": 138.595},
        "Flinders University Stadium": {"lat": -35.200, "lon": 138.560},
        "MA Para Sports Community Stadium": {"lat": -34.846, "lon": 138.496},
        "Ikon Park": {"lat": -37.774, "lon": 144.942},
        "Avalon Airport Oval": {"lat": -38.157, "lon": 144.355},
        "ETU Stadium": {"lat": -38.157, "lon": 144.355},
        "Casey Fields": {"lat": -38.190, "lon": 145.170},
        "Punt Road Oval": {"lat": -37.826, "lon": 144.990},
        "Box Hill City Oval": {"lat": -37.822, "lon": 145.128},
        "Kinetic Stadium": {"lat": -37.917, "lon": 145.128},
        "Whitten Oval": {"lat": -37.837, "lon": 144.870},
        "Mars Stadium": {"lat": -37.565, "lon": 143.839},
        "Queensland Australian Football League": {"lat": -27.470, "lon": 153.021},
        "Bill Pearson Field": {"lat": -27.560, "lon": 152.960},
        "Graham Circular": {"lat": -27.470, "lon": 153.021},
        "Brighton Homes Arena": {"lat": -27.580, "lon": 153.060},
        "Moreton Bay Sports Complex": {"lat": -27.250, "lon": 152.970},
        "Scottsdale Park": {"lat": -41.200, "lon": 146.500},
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

        Falls back to a punctuation-insensitive match so feed variants
        like ``M.C.G.`` resolve to the canonical ``MCG`` entry even when
        no explicit alias exists.

        Args:
            venue: Venue name (canonical or alias)

        Returns:
            Dict with 'lat' and 'lon' keys, or None if unknown.
        """
        # Resolve alias → canonical name
        canonical = self.VENUE_ALIASES.get(venue, venue)
        coords = self.VENUE_COORDS.get(canonical)
        if coords is not None:
            return coords

        # Normalised fallback: ignore punctuation/case ("M.C.G." == "MCG").
        norm = self._normalize_venue(venue)
        if norm:
            for name, c in self.VENUE_COORDS.items():
                if self._normalize_venue(name) == norm:
                    return c
            for alias, name in self.VENUE_ALIASES.items():
                if self._normalize_venue(alias) == norm:
                    return self.VENUE_COORDS.get(name)
        return None

    @staticmethod
    def _normalize_venue(venue: str) -> str:
        """Strip everything but letters/digits and uppercase, for matching."""
        import re

        return re.sub(r"[^A-Z0-9]", "", venue.upper())

    async def resolve_coords(self, venue: str) -> Optional[Dict[str, float]]:
        """Resolve ANY venue — AFL map, league map, then geocoding.

        PARITY-1: the league pipeline fetches weather for grounds this
        class has never heard of, so the resolution chain is:
        the static maps (AFL, then league), then the Open-Meteo
        geocoding API (cached for a day — venue names are stable).
        ``None`` only when the venue resolves nowhere at all.
        """
        static = self._get_venue_coords(venue)
        if static is not None:
            return static

        cache_key = f"weather:geocode:{self._normalize_venue(venue)}"
        cached = await medium_cache.get(cache_key)
        if isinstance(cached, dict) and "lat" in cached:
            return cached

        try:
            response = await self.client.get(
                GEOCODING_BASE_URL,
                params={"name": venue, "count": 5, "language": "en"},
            )
            response.raise_for_status()
            results = (response.json() or {}).get("results") or []
            if results:
                # S-2 (code review): an ambiguous ground name ("Rushton
                # Park") can geocode anywhere on earth — prefer an
                # Australian/NZ hit before falling back to the first
                # result, so a same-named overseas place never wins.
                preferred = next(
                    (
                        r
                        for r in results
                        if str(r.get("country_code", "")).upper()
                        in ("AU", "NZ")
                    ),
                    results[0],
                )
                coords = {
                    "lat": float(preferred["latitude"]),
                    "lon": float(preferred["longitude"]),
                }
                await medium_cache.set(cache_key, coords)
                return coords
        except Exception as e:
            logger.warning(f"Open-Meteo geocoding failed for {venue!r}: {e}")
        return None

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

        Uses the Open-Meteo forecast API.  Venue resolution goes through
        :meth:`resolve_coords` — the AFL map, the league map, then the
        geocoding API — so league grounds resolve too (PARITY-1).

        Args:
            venue: Venue name (canonical or alias)
            days: Number of forecast days (default 7)

        Returns:
            Dict with venue, forecast hourly data, or empty dict.
        """
        coords = await self.resolve_coords(venue)
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
