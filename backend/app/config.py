from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, Field
from typing import List, Union


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./whatismytip.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Union[str, List[str]] = "http://localhost:3000"
    rate_limit_per_minute: int = 60
    squiggle_api_base: str = "https://api.squiggle.com.au"
    
    # OpenRouter Configuration
    openrouter_api_key: str = ""
    openrouter_model: str = "gptoss-120b"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v
    
    @property
    def cors_origins_list(self) -> List[str]:
        if isinstance(self.cors_origins, str):
            return [origin.strip() for origin in self.cors_origins.split(",")]
        return self.cors_origins


settings = Settings()
