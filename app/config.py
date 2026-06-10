from __future__ import annotations

from functools import lru_cache
from os import getenv

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


def _csv_first(value: str | None) -> str | None:
    if not value:
        return None
    return next((item.strip() for item in value.split(",") if item.strip()), None)


def _llm_provider() -> str:
    return getenv("LLM_PROVIDER", "rule_based").lower()


def _llm_api_key() -> str | None:
    if _llm_provider() == "openrouter":
        return getenv("OPENROUTER_API_KEY") or getenv("OPENAI_API_KEY") or None
    return getenv("OPENAI_API_KEY") or None


def _llm_base_url() -> str:
    if _llm_provider() == "openrouter":
        return getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    return getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _llm_model() -> str:
    if _llm_provider() == "openrouter":
        return (
            getenv("OPENROUTER_MODEL")
            or _csv_first(getenv("OPENROUTER_MODELS"))
            or getenv("OPENAI_MODEL")
            or "nvidia/nemotron-3-super-120b-a12b:free"
        )
    return getenv("OPENAI_MODEL", "gpt-4o-mini")


class Settings(BaseModel):
    cal_api_key: str | None = getenv("CAL_API_KEY") or getenv("TXY-CAL-API1") or None
    cal_api_base_url: str = getenv("CAL_API_BASE_URL", "https://api.cal.com").rstrip("/")
    cal_default_timezone: str = getenv("CAL_DEFAULT_TIMEZONE", "America/New_York")
    cal_default_attendee_name: str = getenv("CAL_DEFAULT_ATTENDEE_NAME", "CalAssist User")
    cal_default_attendee_email: str | None = getenv("CAL_DEFAULT_ATTENDEE_EMAIL") or None
    cal_event_type_id: int | None = int(getenv("CAL_EVENT_TYPE_ID")) if getenv("CAL_EVENT_TYPE_ID") else None
    cal_event_type_slug: str | None = getenv("CAL_EVENT_TYPE_SLUG") or None
    cal_username: str | None = getenv("CAL_USERNAME") or None
    cal_organization_slug: str | None = getenv("CAL_ORGANIZATION_SLUG") or None
    cal_send_length_in_minutes: bool = getenv("CAL_SEND_LENGTH_IN_MINUTES", "").lower() in {"1", "true", "yes"}
    llm_provider: str = _llm_provider()
    openai_api_key: str | None = _llm_api_key()
    openai_base_url: str = _llm_base_url()
    openai_model: str = _llm_model()

    @property
    def has_cal_credentials(self) -> bool:
        return bool(self.cal_api_key)

    @property
    def has_booking_target(self) -> bool:
        return bool(self.cal_event_type_id or (self.cal_event_type_slug and self.cal_username))


@lru_cache
def get_settings() -> Settings:
    return Settings()
