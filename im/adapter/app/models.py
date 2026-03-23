from pydantic import BaseModel, Field


class UserSyncRequest(BaseModel):
    external_id: str = Field(..., min_length=1)
    username: str = Field(..., min_length=2)
    display_name: str | None = None
    email: str | None = None
    account_type: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    password: str | None = None
    must_change_password: bool = False
    language: str | None = None
    visibility: int = 1
    status: int = 1
    tags: list[str] = Field(default_factory=list)


class SyncUserResponse(BaseModel):
    external_id: str
    username: str
    mapped_humhub_user_id: int
    created: bool = False
    updated: bool = False
    auth_client_linked: bool = False
    note: str | None = None
    raw: dict = Field(default_factory=dict)


class FeedPostRequest(BaseModel):
    message: str = Field(..., min_length=1)
    space_id: int | None = None
    created_by: int | None = None


class DmSendRequest(BaseModel):
    thread_id: int | None = None
    recipient_user_ids: list[int] = Field(default_factory=list)
    sender_user_id: int | None = None
    message: str = Field(..., min_length=1)


class ApiMessage(BaseModel):
    status: str = 'ok'
    detail: str | None = None
    data: dict | list | None = None
