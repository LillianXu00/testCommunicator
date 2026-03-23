from __future__ import annotations

from fastapi import APIRouter, Depends

from adapter.app.config import Settings, get_settings
from adapter.app.humhub_client import HumHubClient
from adapter.app.models import (
    DmSendRequest,
    DmSendResponse,
    FeedPostRequest,
    FeedPostResponse,
    SyncUserRequest,
    SyncUserResponse,
)
from adapter.app.services.user_service import UserService

router = APIRouter(prefix="/v1", tags=["users"])


def get_humhub_client(settings: Settings = Depends(get_settings)) -> HumHubClient:
    return HumHubClient(settings.humhub_base_url, settings.humhub_api_token)


def get_user_service(
    settings: Settings = Depends(get_settings),
    client: HumHubClient = Depends(get_humhub_client),
) -> UserService:
    return UserService(humhub_client=client, settings=settings)


@router.post("/users/sync", response_model=SyncUserResponse)
async def sync_user(request: SyncUserRequest, service: UserService = Depends(get_user_service)) -> SyncUserResponse:
    return await service.sync_user(request)


@router.post("/feed/post", response_model=FeedPostResponse)
async def feed_post(request: FeedPostRequest, settings: Settings = Depends(get_settings), client: HumHubClient = Depends(get_humhub_client)) -> FeedPostResponse:
    sender_user_id = request.created_by or settings.humhub_system_user_id
    raw = await client.create_feed_post(
        message=request.message,
        created_by=sender_user_id,
        space_id=request.space_id,
        topic=request.topic,
    )
    note = (
        "Used provided HumHub sender user id."
        if request.created_by
        else "Fell back to system user. In production, sync user first and send with mapped_humhub_user_id."
    )
    return FeedPostResponse(status="sent", sender_user_id=sender_user_id, note=note, raw=raw)


@router.post("/dm/send", response_model=DmSendResponse)
async def dm_send(request: DmSendRequest, settings: Settings = Depends(get_settings), client: HumHubClient = Depends(get_humhub_client)) -> DmSendResponse:
    sender_user_id = request.sender_user_id or settings.humhub_system_user_id
    raw = await client.send_dm(
        sender_user_id=sender_user_id,
        recipient_user_id=request.recipient_user_id,
        message=request.message,
    )
    note = (
        "Used provided HumHub sender user id."
        if request.sender_user_id
        else "Fell back to system user. In production, sync user first and send with mapped_humhub_user_id."
    )
    return DmSendResponse(
        status="sent",
        sender_user_id=sender_user_id,
        recipient_user_id=request.recipient_user_id,
        note=note,
        raw=raw,
    )
