"""Response schemas for the team identity endpoint (TEAM-IDENTITY, 2026-09-30).

Exposes the club identity the ingestion captured on the ``teams``
extension rows (migration 0011): crest URL, club colours, abbreviation.
Identity fields are ``None`` when the feed supplied nothing for a
field — the team is still returned and the frontend falls back to its
badge renderer.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


# TEAM-IDENTITY (2026-09-30, user request): crest/colour payload for the
# public team identity endpoint; NULLable fields pass through untouched.
class TeamIdentityResponse(BaseModel):
    """Club identity for one team-kind participant.

    Fields mirror the ``teams`` extension columns; ``name`` comes from
    the ``participants`` row.
    """

    name: str
    abbreviation: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None


class TeamsIdentityResponse(BaseModel):
    """Envelope for ``GET /api/teams`` — all team participants, name-ordered."""

    teams: List[TeamIdentityResponse]


__all__ = [
    "TeamIdentityResponse",
    "TeamsIdentityResponse",
]
