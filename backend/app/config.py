from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./whatismytip.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: List[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 60
    squiggle_api_base: str = "https://api.squiggle.com.au"
    
    # OpenRouter Configuration
    openrouter_api_key: str = ""
    openrouter_model: str = "gptoss-120b"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
