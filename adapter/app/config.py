from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    humhub_base_url: str = Field(..., alias="HUMHUB_BASE_URL")
    humhub_api_token: str = Field(..., alias="HUMHUB_API_TOKEN")
    humhub_system_user_id: int = Field(1, alias="HUMHUB_SYSTEM_USER_ID")
    humhub_authclient_name: str = Field("openclaw", alias="HUMHUB_AUTHCLIENT_NAME")
    humhub_default_user_password_prefix: str = Field(
        "OpenClaw-Init-",
        alias="HUMHUB_DEFAULT_USER_PASSWORD_PREFIX",
    )

    adapter_bind_host: str = Field("0.0.0.0", alias="ADAPTER_BIND_HOST")
    adapter_bind_port: int = Field(8000, alias="ADAPTER_BIND_PORT")

    def placeholder_email(self, external_id: str) -> str:
        return f"{self.safe_external_id(external_id)}@agents.local"

    def default_user_password(self, external_id: str) -> str:
        return f"{self.humhub_default_user_password_prefix}{self.safe_external_id(external_id)}"

    @staticmethod
    def safe_external_id(external_id: str) -> str:
        return "".join(ch if ch.isalnum() else "-" for ch in external_id.lower()).strip("-") or "openclaw-user"


@lru_cache
def get_settings() -> Settings:
    return Settings()
