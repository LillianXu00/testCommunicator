from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'im-adapter'
    app_env: str = Field(default='production', alias='APP_ENV')
    log_level: str = Field(default='INFO', alias='ADAPTER_LOG_LEVEL')
    adapter_bearer_token: str = Field(alias='ADAPTER_BEARER_TOKEN')
    humhub_base_url: str = Field(default='http://humhub', alias='HUMHUB_BASE_URL')
    humhub_service_account_token: str = Field(alias='HUMHUB_SERVICE_ACCOUNT_TOKEN')
    humhub_service_account_guid: str | None = Field(default=None, alias='HUMHUB_SERVICE_ACCOUNT_GUID')
    humhub_default_space_id: int = Field(default=1, alias='HUMHUB_DEFAULT_SPACE_ID')
    humhub_authclient_name: str = Field(default='openclaw', alias='HUMHUB_AUTHCLIENT_NAME')
    humhub_default_user_password_prefix: str = Field(default='OpenClaw-Init-', alias='HUMHUB_DEFAULT_USER_PASSWORD_PREFIX')
    adapter_default_timeout: int = Field(default=20, alias='ADAPTER_DEFAULT_TIMEOUT')
    openclaw_default_external_id_prefix: str = Field(default='openclaw', alias='OPENCLAW_DEFAULT_EXTERNAL_ID_PREFIX')


@lru_cache
def get_settings() -> Settings:
    return Settings()
