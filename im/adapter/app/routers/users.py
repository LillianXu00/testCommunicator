from fastapi import APIRouter, Depends

from ..auth import require_bearer_token
from ..dependencies import get_user_service
from ..models import SyncUserResponse, UserSyncRequest

router = APIRouter(prefix='/v1/users', tags=['users'], dependencies=[Depends(require_bearer_token)])


@router.post('/sync', response_model=SyncUserResponse)
async def sync_user(payload: UserSyncRequest) -> SyncUserResponse:
    return await get_user_service().sync_user(payload)
