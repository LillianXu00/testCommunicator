import logging
from typing import Any

import httpx

from .config import Settings
from .exceptions import HumHubAPIError


logger = logging.getLogger(__name__)


class HumHubClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.humhub_base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {settings.humhub_service_account_token}',
            'Accept': 'application/json',
        }
        self.timeout = settings.adapter_default_timeout

    async def _request(self, method: str, path: str, *, acceptable_statuses: set[int] | None = None, **kwargs: Any) -> Any:
        acceptable_statuses = acceptable_statuses or set()
        url = f'{self.base_url}{path}'
        headers = {**self.headers, **kwargs.pop('headers', {})}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HumHubAPIError(f'failed to reach HumHub: {exc}') from exc

        if response.status_code in acceptable_statuses:
            return None

        if response.status_code >= 400:
            logger.error(
                'humhub_request_failed',
                extra={'status_code': response.status_code, 'body': response.text, 'url': url, 'method': method},
            )
            raise HumHubAPIError(f'HumHub API returned {response.status_code}: {response.text[:300]}')

        if 'application/json' in response.headers.get('content-type', ''):
            return response.json()
        return {'raw': response.text, 'status_code': response.status_code}

    async def list_feed(self, limit: int = 20) -> Any:
        return await self._request('GET', '/api/v1/content/container/root/stream', params={'limit': limit})

    async def post_feed(self, message: str, space_id: int | None = None, created_by: int | None = None) -> Any:
        path = f'/api/v1/space/{space_id}/content' if space_id else '/api/v1/post'
        payload: dict[str, Any] = {
            'message': message,
            'createdBy': created_by,
            'created_by': created_by,
        }
        headers: dict[str, str] = {}
        if created_by is not None:
            headers['X-HumHub-Act-As-User-Id'] = str(created_by)
        return await self._request('POST', path, json={k: v for k, v in payload.items() if v is not None}, headers=headers)

    async def list_spaces(self) -> Any:
        return await self._request('GET', '/api/v1/space')

    async def get_user_by_authclient(self, name: str, source_id: str) -> Any | None:
        return await self._request(
            'GET',
            '/api/v1/user/get-by-authclient',
            params={'name': name, 'id': source_id},
            acceptable_statuses={404},
        )

    async def get_user_by_username(self, username: str) -> Any | None:
        return await self._request(
            'GET',
            '/api/v1/user/get-by-username',
            params={'username': username},
            acceptable_statuses={404},
        )

    async def get_user_by_email(self, email: str) -> Any | None:
        return await self._request(
            'GET',
            '/api/v1/user/get-by-email',
            params={'email': email},
            acceptable_statuses={404},
        )

    async def create_user(self, *, account: dict[str, Any], profile: dict[str, Any], password: dict[str, Any]) -> Any:
        return await self._request('POST', '/api/v1/user', json={'account': account, 'profile': profile, 'password': password})

    async def update_user(
        self,
        user_id: int,
        *,
        account: dict[str, Any],
        profile: dict[str, Any],
        password: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {'account': account, 'profile': profile}
        if password is not None:
            payload['password'] = password
        return await self._request('PUT', f'/api/v1/user/{user_id}', json=payload)

    async def add_auth_client(self, user_id: int, source: str, source_id: str) -> Any:
        result = await self._request(
            'POST',
            f'/api/v1/user/{user_id}/auth-client',
            json={'source': source, 'sourceId': source_id},
            acceptable_statuses={409, 422},
        )
        return result or {'status': 'already-linked'}

    async def list_dm_threads(self) -> Any:
        return await self._request('GET', '/api/v1/mail/message')

    async def read_dm_thread(self, thread_id: int) -> Any:
        return await self._request('GET', f'/api/v1/mail/message/{thread_id}')

    async def send_dm(
        self,
        message: str,
        *,
        thread_id: int | None = None,
        recipient_user_ids: list[int] | None = None,
        sender_user_id: int | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            'message': message,
            'recipientUserIds': recipient_user_ids or [],
            'senderUserId': sender_user_id,
            'sender_user_id': sender_user_id,
        }
        headers: dict[str, str] = {}
        if sender_user_id is not None:
            headers['X-HumHub-Act-As-User-Id'] = str(sender_user_id)
        if thread_id is not None:
            return await self._request(
                'POST',
                f'/api/v1/mail/message/{thread_id}',
                json={k: v for k, v in payload.items() if v is not None and k not in {'recipientUserIds'}},
                headers=headers,
            )
        return await self._request(
            'POST',
            '/api/v1/mail/message',
            json={k: v for k, v in payload.items() if v is not None},
            headers=headers,
        )
