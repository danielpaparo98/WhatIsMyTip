from datetime import datetime
from typing import List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_season() -> int:
    now = datetime.now()
    # AFL season runs roughly March-September
    # If we're in Oct-Feb, we're in off-season, still reference current year
    return now.year


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/whatismytip"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: Union[str, List[str]] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origins"
    )
    rate_limit_per_minute: int = 60
    squiggle_api_base: str = "https://api.squiggle.com.au"
    squiggle_contact_email: str = "contact@whatismytip.com"

    # OpenRouter Configuration
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    environment: str = "development"
    admin_api_key: str = ""  # Set via ADMIN_API_KEY env var

    # Cron Job Configuration
    cron_enabled: bool = True
    cron_timezone: str = "Australia/Perth"

    # Daily Sync Configuration
    current_season: int = Field(default_factory=_default_season)
    daily_sync_enabled: bool = True

    # Game Sync (frequent to keep live round data fresh)
    cron_daily_sync: str = "*/15 * * * *"  # Every 15 minutes
    daily_sync_timeout_seconds: int = 3600  # 1 hour

    # Match Completion Detector
    cron_match_completion_check: str = "5,20,35,50 * * * *"  # Every 15 min, offset by 5
    match_completion_buffer_minutes: int = 60  # 1 hour buffer
    match_completion_check_enabled: bool = True  # Enable/disable the job
    completion_check_timeout_seconds: int = 300  # 5 minutes

    # Tip Generation
    # NOTE: cron expressions here are for documentation only — the actual trigger
    # schedule is configured in project.yml:
    #   tip-generation:  "0 19 * * *"  (= 3:00 AM AWST, UTC+8)
    cron_tip_generation: str = "0 3 * * *"  # 3:00 AM AWST (= 19:00 UTC)
    tip_generation_timeout_seconds: int = 1800  # 30 minutes
    tip_generation_enabled: bool = True
    tip_generation_regenerate_existing: bool = False

    # Historical Data Refresh
    # NOTE: actual trigger schedule in project.yml:
    #   historic-refresh:  "0 20 * * 6"  (= 4:00 AM AWST Sunday, UTC+8)
    cron_historical_refresh: str = "0 4 * * 0"  # 4:00 AM AWST Sunday (= 20:00 UTC Saturday)
    historic_refresh_enabled: bool = True
    historic_refresh_seasons: str = "2010-2025"
    historic_refresh_regenerate_tips: bool = False
    historical_refresh_start_year: int = 2010
    historical_refresh_timeout_seconds: int = 900  # 15 minutes (DO Functions hard limit)

    # Retry Configuration
    job_timeout_seconds: int = 3600
    # Lock expiry should match the platform timeout (900s), not the runtime budget.
    # DO Functions enforces a hard 15-minute ceiling; set locks to 900s max.
    job_lock_expire_seconds: int = 900
    job_max_retries: int = 3
    job_retry_delay_seconds: int = 60

    # Alerting Configuration
    alert_enabled: bool = False
    alert_webhook_url: Optional[str] = None
    alert_email_recipients: List[str] = []
    alert_timeout_seconds: int = 10  # webhook timeout

    # Monitoring Configuration
    metrics_enabled: bool = True
    metrics_retention_days: int = 30

    # Security Configuration
    rate_limit_max_requests: int = 60
    rate_limit_window_seconds: int = 60
    max_request_body_bytes: int = 5242880  # 5 MB

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
