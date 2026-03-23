from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapter.app.config import Settings
from adapter.app.humhub_client import HumHubClient
from adapter.app.models import SyncUserRequest, SyncUserResponse


@dataclass(slots=True)
class UserService:
    humhub_client: HumHubClient
    settings: Settings

    async def sync_user(self, request: SyncUserRequest) -> SyncUserResponse:
        email = request.email or self.settings.placeholder_email(request.external_id)
        password = request.password or self.settings.default_user_password(request.external_id)
        account_payload = {
            "email": email,
            "status": request.status,
            "visibility": request.visibility,
            "tags": request.tags,
            "account_type": request.account_type,
            "mustChangePassword": request.must_change_password,
        }
        profile_payload = {
            "displayName": request.display_name,
            "firstname": request.first_name,
            "lastname": request.last_name,
        }

        raw: dict[str, Any] = {"lookup": {}, "actions": []}

        existing = await self.humhub_client.get_user_by_authclient(self.settings.humhub_authclient_name, request.external_id)
        raw["lookup"]["authclient"] = existing
        user_id = self._extract_user_id(existing)
        if user_id is not None:
            update_payload = await self.humhub_client.update_user(
                user_id,
                username=request.username,
                email=email,
                account=account_payload,
                profile=profile_payload,
                language=request.language,
                visibility=request.visibility,
                status=request.status,
                tags=request.tags,
            )
            raw["actions"].append({"update_user": update_payload})
            return SyncUserResponse(
                external_id=request.external_id,
                username=request.username,
                mapped_humhub_user_id=user_id,
                created=False,
                updated=True,
                auth_client_linked=True,
                note="Updated existing HumHub user matched by auth client.",
                raw=raw,
            )

        username_hit = await self.humhub_client.get_user_by_username(request.username)
        raw["lookup"]["username"] = username_hit
        user_id = self._extract_user_id(username_hit)

        if user_id is None and email:
            email_hit = await self.humhub_client.get_user_by_email(email)
            raw["lookup"]["email"] = email_hit
            user_id = self._extract_user_id(email_hit)

        if user_id is not None:
            update_payload = await self.humhub_client.update_user(
                user_id,
                username=request.username,
                email=email,
                account=account_payload,
                profile=profile_payload,
                language=request.language,
                visibility=request.visibility,
                status=request.status,
                tags=request.tags,
            )
            auth_link_payload = await self.humhub_client.add_auth_client(
                user_id,
                self.settings.humhub_authclient_name,
                request.external_id,
            )
            raw["actions"].append({"update_user": update_payload})
            raw["actions"].append({"add_auth_client": auth_link_payload})
            return SyncUserResponse(
                external_id=request.external_id,
                username=request.username,
                mapped_humhub_user_id=user_id,
                created=False,
                updated=True,
                auth_client_linked=True,
                note="Updated legacy HumHub user and linked auth client.",
                raw=raw,
            )

        create_payload = await self.humhub_client.create_user(
            username=request.username,
            email=email,
            password=password,
            account=account_payload,
            profile=profile_payload,
        )
        raw["actions"].append({"create_user": create_payload})
        created_user_id = self._extract_user_id(create_payload)
        if created_user_id is None:
            raise RuntimeError("HumHub create_user response did not include a user id")

        auth_link_payload = await self.humhub_client.add_auth_client(
            created_user_id,
            self.settings.humhub_authclient_name,
            request.external_id,
        )
        raw["actions"].append({"add_auth_client": auth_link_payload})
        return SyncUserResponse(
            external_id=request.external_id,
            username=request.username,
            mapped_humhub_user_id=created_user_id,
            created=True,
            updated=False,
            auth_client_linked=True,
            note="Created HumHub user and linked auth client.",
            raw=raw,
        )

    @staticmethod
    def _extract_user_id(payload: dict[str, Any]) -> int | None:
        if not payload:
            return None
        for candidate in (payload.get("id"), payload.get("user", {}).get("id") if isinstance(payload.get("user"), dict) else None):
            if isinstance(candidate, int):
                return candidate
        items = payload.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict) and isinstance(first.get("id"), int):
                return first["id"]
        return None
