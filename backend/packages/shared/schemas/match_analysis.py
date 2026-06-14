from datetime import datetime

from pydantic import BaseModel


class MatchAnalysisResponse(BaseModel):
    id: int
    game_id: int
    analysis_text: str
    created_at: datetime

    class Config:
        from_attributes = True
