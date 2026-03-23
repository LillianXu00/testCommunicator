from functools import lru_cache

from .config import Settings, get_settings
from .humhub_client import HumHubClient
from .services.user_service import UserService


@lru_cache
def get_humhub_client() -> HumHubClient:
    return HumHubClient(get_settings())


@lru_cache
def get_user_service() -> UserService:
    settings: Settings = get_settings()
    return UserService(get_humhub_client(), settings)
