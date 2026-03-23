import logging
import re
from hashlib import sha1
from typing import Any

from ..config import Settings
from ..humhub_client import HumHubClient
from ..models import SyncUserResponse, UserSyncRequest


logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, client: HumHubClient, settings: Settings):
        self.client = client
        self.settings = settings

    def _safe_external_id(self, external_id: str) -> str:
        cleaned = re.sub(r'[^a-zA-Z0-9._-]+', '-', external_id.strip().lower()).strip('-')
        if cleaned:
            return cleaned[:48]
        return sha1(external_id.encode('utf-8')).hexdigest()[:24]

    def _default_email(self, external_id: str) -> str:
        return f'{self._safe_external_id(external_id)}@agents.local'

    def _default_password(self, external_id: str) -> str:
        return f'{self.settings.humhub_default_user_password_prefix}{self._safe_external_id(external_id)}'

    def _display_name(self, payload: UserSyncRequest) -> str:
        if payload.display_name:
            return payload.display_name
        full_name = ' '.join(part for part in [payload.first_name, payload.last_name] if part)
        return full_name or payload.username

    def _build_account(self, payload: UserSyncRequest, email: str) -> dict[str, Any]:
        account: dict[str, Any] = {
            'username': payload.username,
            'email': email,
            'visibility': payload.visibility,
            'status': payload.status,
            'language': payload.language or 'en',
            'tagsField': payload.tags,
        }
        if payload.account_type:
            account['accountType'] = payload.account_type
        return account

    def _build_profile(self, payload: UserSyncRequest) -> dict[str, Any]:
        profile: dict[str, Any] = {
            'displayName': self._display_name(payload),
            'firstname': payload.first_name or payload.username,
            'lastname': payload.last_name or '',
        }
        if payload.account_type:
            profile['title'] = payload.account_type
        return profile

    def _build_password(self, payload: UserSyncRequest) -> dict[str, Any]:
        return {
            'newPassword': payload.password or self._default_password(payload.external_id),
            'mustChangePassword': payload.must_change_password,
        }

    @staticmethod
    def _extract_user_id(raw_user: dict[str, Any]) -> int:
        if not raw_user:
            raise ValueError('HumHub user payload is empty')
        candidates = [
            raw_user.get('id'),
            raw_user.get('account', {}).get('id'),
            raw_user.get('user', {}).get('id'),
        ]
        for candidate in candidates:
            if candidate is not None:
                return int(candidate)
        raise ValueError(f'Unable to extract user id from payload: {raw_user}')

    async def sync_user(self, payload: UserSyncRequest) -> SyncUserResponse:
        email = payload.email or self._default_email(payload.external_id)
        account = self._build_account(payload, email)
        profile = self._build_profile(payload)
        password = self._build_password(payload)
        authclient_name = self.settings.humhub_authclient_name

        raw: dict[str, Any] = {
            'lookups': {},
            'writes': {},
            'normalized': {
                'email': email,
                'password_seeded': payload.password is None,
                'authclient_name': authclient_name,
            },
        }

        user = await self.client.get_user_by_authclient(authclient_name, payload.external_id)
        raw['lookups']['authclient'] = user
        if user:
            user_id = self._extract_user_id(user)
            updated = await self.client.update_user(user_id, account=account, profile=profile, password=password)
            raw['writes']['update'] = updated
            logger.info('user_synced_via_authclient', extra={'external_id': payload.external_id, 'user_id': user_id})
            return SyncUserResponse(
                external_id=payload.external_id,
                username=payload.username,
                mapped_humhub_user_id=user_id,
                updated=True,
                auth_client_linked=True,
                note='matched existing HumHub user by auth client and updated profile',
                raw=raw,
            )

        user = await self.client.get_user_by_username(payload.username)
        raw['lookups']['username'] = user
        if not user and email:
            user = await self.client.get_user_by_email(email)
            raw['lookups']['email'] = user

        if user:
            user_id = self._extract_user_id(user)
            updated = await self.client.update_user(user_id, account=account, profile=profile, password=password)
            linked = await self.client.add_auth_client(user_id, authclient_name, payload.external_id)
            raw['writes']['update'] = updated
            raw['writes']['auth_client_link'] = linked
            logger.info('user_synced_via_existing_match', extra={'external_id': payload.external_id, 'user_id': user_id})
            return SyncUserResponse(
                external_id=payload.external_id,
                username=payload.username,
                mapped_humhub_user_id=user_id,
                updated=True,
                auth_client_linked=True,
                note='matched existing HumHub user by username/email, updated it, then linked auth client',
                raw=raw,
            )

        created = await self.client.create_user(account=account, profile=profile, password=password)
        user_id = self._extract_user_id(created)
        linked = await self.client.add_auth_client(user_id, authclient_name, payload.external_id)
        raw['writes']['create'] = created
        raw['writes']['auth_client_link'] = linked
        logger.info('user_created_and_linked', extra={'external_id': payload.external_id, 'user_id': user_id})
        return SyncUserResponse(
            external_id=payload.external_id,
            username=payload.username,
            mapped_humhub_user_id=user_id,
            created=True,
            auth_client_linked=True,
            note='created a real HumHub user and linked openclaw auth client identity',
            raw=raw,
        )
