"""Pydantic request schemas for admin API endpoints."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DailySyncTriggerRequest(BaseModel):
    """Request model for triggering daily sync."""

    model_config = ConfigDict(extra="ignore")

    season: Optional[int] = Field(
        default=None,
        description="Season year to sync. Defaults to current season.",
    )


class MatchCompletionTriggerRequest(BaseModel):
    """Request model for triggering match completion detection."""

    model_config = ConfigDict(extra="ignore")

    season: Optional[int] = Field(
        default=None,
        description="Season year to check. Defaults to current season.",
    )
    buffer_minutes: Optional[int] = Field(
        default=None,
        description="Buffer in minutes after match start before checking completion.",
    )


class TipGenerationTriggerRequest(BaseModel):
    """Request model for triggering tip generation."""

    model_config = ConfigDict(extra="ignore")

    season: Optional[int] = Field(
        default=None,
        description="Season year. If omitted with round_id, uses next upcoming round.",
    )
    round_id: Optional[int] = Field(
        default=None,
        description="Round number to generate tips for.",
    )
    regenerate: bool = Field(
        default=False,
        description="If true, regenerate tips even if they already exist.",
    )


class HistoricRefreshTriggerRequest(BaseModel):
    """Request model for triggering historic data refresh."""

    model_config = ConfigDict(extra="ignore")

    seasons: Optional[str] = Field(
        default=None,
        description="Comma-separated season years. Defaults to configured historic seasons.",
    )
    round_id: Optional[int] = Field(
        default=None,
        description="Specific round to refresh.",
    )
    regenerate_tips: bool = Field(
        default=False,
        description="If true, regenerate tips during the refresh.",
    )


class TipGenerateRequest(BaseModel):
    """Request model for the tips /generate endpoint."""

    model_config = ConfigDict(extra="ignore")

    season: int = Field(
        ...,
        description="Season year (required).",
    )
    round_id: Optional[int] = Field(
        default=None,
        description="Round number. If omitted, both season and round must come from query.",
    )
    regenerate: bool = Field(
        default=False,
        description="If true, regenerate tips even if they already exist.",
    )
    heuristics: Optional[List[str]] = Field(
        default=None,
        description=(
            "List of heuristic types to generate. Must be from the allowed "
            "set (best_bet, weighted_tip, yolo)."
        ),
    )
