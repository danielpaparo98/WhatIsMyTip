from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, Field
from typing import List, Union, Optional


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./whatismytip.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Union[str, List[str]] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins"
    )
    rate_limit_per_minute: int = 60
    squiggle_api_base: str = "https://api.squiggle.com.au"
    squiggle_contact_email: str = "contact@whatismytip.com"
    
    # OpenRouter Configuration
    openrouter_api_key: str = ""
    openrouter_model: str = "gptoss-120b"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    
    environment: str = "development"
    
    # Cron Job Configuration
    cron_enabled: bool = True
    cron_timezone: str = "Australia/Perth"
    
    # Daily Sync Configuration
    current_season: int = 2026
    daily_sync_enabled: bool = True
    
    # Daily Game Sync
    cron_daily_sync: str = "0 2 * * *"  # 2:00 AM daily
    daily_sync_timeout_seconds: int = 3600  # 1 hour
    
    # Match Completion Detector
    cron_match_completion_check: str = "*/15 * * * *"  # Every 15 minutes
    match_completion_buffer_minutes: int = 60  # 1 hour buffer
    match_completion_check_enabled: bool = True  # Enable/disable the job
    completion_check_timeout_seconds: int = 300  # 5 minutes
    
    # Tip Generation
    cron_tip_generation: str = "0 3 * * *"  # 3:00 AM daily
    tip_generation_timeout_seconds: int = 1800  # 30 minutes
    
    # Historical Data Refresh
    cron_historical_refresh: str = "0 4 * * 0"  # Sunday 4:00 AM
    historical_refresh_start_year: int = 2010
    historical_refresh_timeout_seconds: int = 7200  # 2 hours
    
    # Retry Configuration
    job_timeout_seconds: int = 3600
    job_lock_expire_seconds: int = 7200
    job_max_retries: int = 3
    job_retry_delay_seconds: int = 60
    
    # Alerting Configuration
    alert_enabled: bool = False
    alert_webhook_url: Optional[str] = None
    alert_email_recipients: List[str] = []
    
    # Monitoring Configuration
    metrics_enabled: bool = True
    metrics_retention_days: int = 30

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
