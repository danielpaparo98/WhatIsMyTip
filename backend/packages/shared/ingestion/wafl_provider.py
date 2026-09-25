"""WaflProvider — the WAFL FeedProvider (Phase 5 pilot).

A configured :class:`SportixProvider`: the official WAFL site
(wafl.com.au) tenant, "League" competition.  WAFLW / Colts / Reserves
on the same tenant are additional constructor calls — see
:class:`SportixProvider`.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from .sportix_provider import (
    FINALS_ROUND_MAP,
    SportixProvider,
    round_id_from_slug,
)

_SOURCE = "sportix-wafl"


class WaflProvider(SportixProvider):
    """FeedProvider for the WAFL League competition."""

    sport_id = "afl"
    source = _SOURCE

    def __init__(
        self,
        competition_name: str = "League",
        fetch_json: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        super().__init__(
            source=_SOURCE,
            competition_name=competition_name,
            fetch_json=fetch_json,
        )


__all__ = ["WaflProvider"]
