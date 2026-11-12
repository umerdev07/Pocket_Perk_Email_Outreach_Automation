from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    claude_api_key: str = ""
    claude_model: str = ""
    outreach_sender_name: str = "The PocketPerks Team"
    outreach_company_name: str = "VenturesSky Ltd"
    outreach_website: str = "https://pocketperks.co.uk"
    outreach_phone: str = ""
    instantly_api_key: str = ""
    instantly_webhook_secret: str = ""
    lead_eligibility_mode: Literal["advisory", "strict"] = "advisory"
    instantly_auto_launch: bool = False
    send_mode: Literal["test", "live"] = "test"
    frontend_origin: str = "http://localhost:3000"
    # Keep provider load bounded; tune this to the configured Claude model's limits.
    personalization_concurrency: int = Field(default=1, ge=1, le=10)
    personalization_batch_size: int = Field(default=25, ge=1, le=250)
    # Operational target for new first-touch leads; follow-up sends are additional.
    outreach_new_leads_per_day: int = Field(default=100, ge=1, le=100000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
