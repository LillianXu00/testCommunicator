from dataclasses import dataclass
from .config import Settings


@dataclass
class UserMappingResult:
    mode: str
    external_id: str
    humhub_user_id: int | None = None


class UserMappingService:
    """MVP mapping service.

    Current mode uses a single service account for API calls, while keeping an
    explicit extension point for future per-user mapping.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve_actor(self, external_id: str) -> UserMappingResult:
        return UserMappingResult(mode='service-account', external_id=external_id, humhub_user_id=None)
