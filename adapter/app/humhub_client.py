from __future__ import annotations

from typing import Any

import httpx


class HumHubClient:
    def __init__(self, base_url: str, api_token: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers=headers) as client:
            response = await client.request(method, path, params=params, json=json)

        if response.status_code == 404:
            return {}

        response.raise_for_status()
        if not response.content:
            return {}
        data = response.json()
        if isinstance(data, dict):
            return data
        return {"items": data}

    async def get_user_by_authclient(self, name: str, source_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/user/get-by-authclient",
            params={"name": name, "id": source_id},
        )

    async def get_user_by_username(self, username: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/user", params={"username": username})

    async def get_user_by_email(self, email: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/user", params={"email": email})

    async def create_user(self, *, username: str, email: str, password: str, account: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/user",
            json={
                "account": {"username": username, "email": email, "password": password, **account},
                "profile": profile,
                "password": password,
            },
        )

    async def update_user(self, user_id: int, *, username: str, email: str, account: dict[str, Any], profile: dict[str, Any], language: str | None, visibility: int | None, status: int | None, tags: list[str]) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/api/v1/user/{user_id}",
            json={
                "account": {"username": username, "email": email, **account},
                "profile": {**profile, "tags": tags},
                "language": language,
                "visibility": visibility,
                "status": status,
                "tags": tags,
            },
        )

    async def add_auth_client(self, user_id: int, source: str, source_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/user/{user_id}/auth-client",
            json={"source": source, "sourceId": source_id},
        )

    async def create_feed_post(self, *, message: str, created_by: int, space_id: int | None = None, topic: str | None = None) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/content/feed",
            json={"message": message, "created_by": created_by, "space_id": space_id, "topic": topic},
        )

    async def send_dm(self, *, sender_user_id: int, recipient_user_id: int, message: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/mail/message",
            json={"from": sender_user_id, "recipient": recipient_user_id, "message": message},
        )
