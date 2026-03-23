from functools import lru_cache
from .config import get_settings, Settings
from .humhub_client import HumHubClient
from .user_mapping import UserMappingService


@lru_cache
def get_humhub_client() -> HumHubClient:
    return HumHubClient(get_settings())


@lru_cache
def get_user_mapping_service() -> UserMappingService:
    settings: Settings = get_settings()
    return UserMappingService(settings)
