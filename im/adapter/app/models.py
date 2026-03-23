from pydantic import BaseModel, EmailStr, Field


class UserSyncRequest(BaseModel):
    external_id: str = Field(..., min_length=1)
    username: str = Field(..., min_length=2)
    email: EmailStr
    display_name: str | None = None


class FeedPostRequest(BaseModel):
    message: str = Field(..., min_length=1)
    space_id: int | None = None


class DmSendRequest(BaseModel):
    thread_id: int | None = None
    recipient_user_ids: list[int] = Field(default_factory=list)
    message: str = Field(..., min_length=1)


class ApiMessage(BaseModel):
    status: str = 'ok'
    detail: str | None = None
    data: dict | list | None = None
