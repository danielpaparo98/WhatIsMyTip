from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class MatchAnalysisResponse(BaseModel):
    id: int
    game_id: int
    analysis_text: str
    created_at: datetime

    class Config:
        from_attributes = True
