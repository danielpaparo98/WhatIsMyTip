"""Client for The Odds API v4 (https://the-odds-api.com).

BT-ODDS (2026-10 review): backtests must settle profit at tipping odds,
not a constant return.  This client fetches the AFL head-to-head market
from AU bookmakers and normalises each event to a **median decimal
price per side** — the median is robust to the outlier prices some
books post.

Usage/cost model: one request covers all live+upcoming games for the
sport, so a single daily snapshot costs ~30 credits/month (free tier =
500/month).  The quota headers (``x-requests-remaining``) are logged so
operators notice a shrinking allowance.

Mirrors :mod:`packages.shared.squiggle.client` in shape: an async
httpx client with ``close()`` / async-context-manager support.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from ..logger import get_logger
from ..teams import canonical_team

logger = get_logger(__name__)

# The Odds API appends marketing suffixes to some club names ('Carlton
# Blues', 'North Melbourne Kangaroos') that teams.py doesn't carry —
# teams.py is pinned by the migration-0007 alias test, so vendor-specific
# suffixes live here.  Plain names ('Sydney Swans', 'West Coast Eagles')
# already resolve through the shared map.  Applied BEFORE canonical_team.
# m-5: every alias here must keep all 18 AFL clubs resolvable — pinned by
# test_all_18_clubs_resolve_through_vendor_aliases.
_ODDS_API_TEAM_ALIASES = {
    "carlton blues": "Carlton",
    "collingwood magpies": "Collingwood",
    "essendon bombers": "Essendon",
    "geelong cats": "Geelong",
    "greater western sydney giants": "GWS Giants",
    "hawthorn hawks": "Hawthorn",
    "kangaroos": "North Melbourne",
    "melbourne demons": "Melbourne",
    "north melbourne kangaroos": "North Melbourne",
    "port adelaide power": "Port Adelaide",
    "richmond tigers": "Richmond",
    "st kilda saints": "St Kilda",
}


def odds_api_canonical_team(name: str | None) -> str:
    """Normalise an Odds API team name to the app's canonical form."""
    if not name:
        return ""
    aliased = _ODDS_API_TEAM_ALIASES.get(name.strip().lower())
    return canonical_team(aliased if aliased is not None else name)


# Decimal prices at or below 1.0 are impossible (a winning $1 bet paying
# nothing) — dropped rather than settled.
_MIN_VALID_DECIMAL_PRICE = 1.01


def _valid_price(price: Any) -> Optional[float]:
    """Coerce an odds price to a sane decimal float, or ``None``."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return value if value >= _MIN_VALID_DECIMAL_PRICE else None


def _median_side_price(
    event: Dict[str, Any],
    outcome_name: str,
) -> Optional[float]:
    """Median decimal price quoted for one side across all bookmakers.

    A bookmaker contributes only when it quotes the requested side in
    its ``h2h`` market; books that skipped the side (or quoted garbage)
    are excluded rather than counted as missing.
    """
    prices: List[float] = []
    wanted = odds_api_canonical_team(outcome_name).lower()
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                if odds_api_canonical_team(outcome.get("name", "")).lower() == wanted:
                    price = _valid_price(outcome.get("price"))
                    if price is not None:
                        prices.append(price)
    return statistics.median(prices) if prices else None


def parse_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one Odds API event to the sync-service shape.

    Team names pass through :func:`canonical_team` so 'Sydney Swans' /
    'West Coast Eagles' / 'GWS Giants' join cleanly against the
    Squiggle-dialect names in ``games``.
    """
    home_name = event.get("home_team") or ""
    away_name = event.get("away_team") or ""

    commence_raw = event.get("commence_time")
    try:
        commence = datetime.fromisoformat(str(commence_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        commence = None

    book_count = len(event.get("bookmakers") or [])
    home_odds = _median_side_price(event, home_name) if home_name else None
    away_odds = _median_side_price(event, away_name) if away_name else None

    return {
        "external_id": str(event.get("id") or ""),
        "commence_time": commence,
        "home_team": odds_api_canonical_team(home_name),
        "away_team": odds_api_canonical_team(away_name),
        "home_odds": home_odds,
        "away_odds": away_odds,
        "bookmaker": f"consensus median ({book_count} books)" if book_count else None,
        "captured_at": datetime.now(timezone.utc),
    }


class OddsAPIClient:
    """Async client for The Odds API v4 AFL head-to-head market."""

    def __init__(self) -> None:
        self.base_url = settings.odds_api_base.rstrip("/")
        self.api_key = settings.odds_api_key
        self.sport_key = settings.odds_api_sport_key
        self.regions = settings.odds_api_regions
        self.client = httpx.AsyncClient(
            timeout=30.0,
            verify=True,
            headers={"User-Agent": "WhatIsMyTip odds-sync"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> "OddsAPIClient":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()

    async def get_afl_head_to_head_odds(self) -> List[Dict[str, Any]]:
        """Fetch normalised AFL head-to-head odds for live+upcoming games.

        Returns:
            List of :func:`parse_event` dicts (median decimal price per
            side, canonical team names, UTC commence time).

        Raises:
            httpx.HTTPError: On transport/HTTP failure — callers let
                BaseJob's retry/backoff handle transient upstream errors.
                The raised message never contains the API key: the vendor
                requires key-in-query auth, and ``raise_for_status``
                embeds the request URL in its message, so it is re-raised
                with a redacted description (M-1).  BaseJob persists
                error strings to ``job_executions`` and alert webhooks.
        """
        url = f"{self.base_url}/sports/{self.sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        response = await self.client.get(url, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # M-1 (code review): ``str(exc)`` includes the full request
            # URL with ``apiKey=...``.  Re-raise with the key stripped so
            # the credential never reaches logs, ``job_executions`` rows,
            # or alert webhooks.
            raise httpx.HTTPStatusError(
                f"Odds API returned HTTP {exc.response.status_code} for {exc.request.url.path}",
                request=exc.request,
                response=exc.response,
            ) from exc

        # Quota headers — log at warning when the free-tier allowance
        # gets low so the sync degrades loudly instead of silently.
        remaining = response.headers.get("x-requests-remaining")
        used = response.headers.get("x-requests-used")
        if remaining is not None:
            try:
                if float(remaining) < 50:
                    logger.warning(
                        "The Odds API quota running low: %s requests remaining "
                        "(%s used this period)",
                        remaining,
                        used,
                    )
                else:
                    logger.debug(
                        "The Odds API quota: %s remaining (%s used)",
                        remaining,
                        used,
                    )
            except ValueError:
                pass

        events = response.json()
        if not isinstance(events, list):
            logger.error("Unexpected Odds API payload shape: %s", type(events).__name__)
            return []

        return [parse_event(e) for e in events]
