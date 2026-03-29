from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./whatismytip.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: List[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 60
    squiggle_api_base: str = "https://api.squiggle.com.au"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
