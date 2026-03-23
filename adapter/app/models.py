from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SyncUserRequest(BaseModel):
    external_id: str = Field(..., min_length=1, description="OpenClaw external identity primary key")
    username: str = Field(..., min_length=3, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    email: EmailStr | None = None
    account_type: Literal["user", "claw", "agent"] = "user"
    first_name: str | None = None
    last_name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    must_change_password: bool = False
    language: str | None = "en-US"
    visibility: int | None = 1
    status: int | None = 1
    tags: list[str] = Field(default_factory=list)

    @field_validator("external_id", "username", "display_name")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be blank")
        return cleaned

    @field_validator("first_name", "last_name", "language")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SyncUserResponse(BaseModel):
    external_id: str
    username: str
    mapped_humhub_user_id: int
    created: bool
    updated: bool
    auth_client_linked: bool
    note: str
    raw: dict[str, Any] = Field(default_factory=dict)


class FeedPostRequest(BaseModel):
    message: str = Field(..., min_length=1)
    created_by: int | None = Field(default=None, description="HumHub user id for the real sender")
    space_id: int | None = None
    topic: str | None = None


class FeedPostResponse(BaseModel):
    status: Literal["queued", "sent"]
    sender_user_id: int
    note: str
    raw: dict[str, Any] = Field(default_factory=dict)


class DmSendRequest(BaseModel):
    recipient_user_id: int
    message: str = Field(..., min_length=1)
    sender_user_id: int | None = Field(default=None, description="HumHub user id for the real sender")


class DmSendResponse(BaseModel):
    status: Literal["queued", "sent"]
    sender_user_id: int
    recipient_user_id: int
    note: str
    raw: dict[str, Any] = Field(default_factory=dict)


class HumHubUserProfile(BaseModel):
    firstname: str | None = None
    lastname: str | None = None
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


class HumHubUserRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    username: str
    email: str | None = None
    profile: HumHubUserProfile | dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    authClients: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
