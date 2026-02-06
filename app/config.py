"""
Configuration management using Pydantic Settings.
Loads environment variables with validation and defaults.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = Field(default="JewelClaw")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Database (Railway provides DATABASE_URL automatically)
    database_url: str = Field(default="sqlite:///./jewelclaw.db")

    # Claude AI
    anthropic_api_key: str = Field(default="")

    # Twilio WhatsApp
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_whatsapp_number: str = Field(default="whatsapp:+14155238886")

    # Timezone
    timezone: str = Field(default="Asia/Kolkata")

    # Morning Brief Schedule
    morning_brief_hour: int = Field(default=8)
    morning_brief_minute: int = Field(default=0)

    # Rate Limits
    max_messages_per_hour: int = Field(default=60)
    scrape_interval_minutes: int = Field(default=15)

    # Test phone number
    test_phone_number: str = Field(default="")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience export
settings = get_settings()
