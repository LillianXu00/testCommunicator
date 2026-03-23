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

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f'{self.base_url}{path}'
        headers = {**self.headers, **kwargs.pop('headers', {})}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HumHubAPIError(f'failed to reach HumHub: {exc}') from exc

        if response.status_code >= 400:
            logger.error('humhub_request_failed', extra={'status_code': response.status_code, 'body': response.text, 'url': url})
            raise HumHubAPIError(f'HumHub API returned {response.status_code}: {response.text[:300]}')

        if 'application/json' in response.headers.get('content-type', ''):
            return response.json()
        return {'raw': response.text}

    async def list_feed(self, limit: int = 20) -> Any:
        return await self._request('GET', '/api/v1/content/container/root/stream', params={'limit': limit})

    async def post_feed(self, message: str, space_id: int | None = None) -> Any:
        if space_id:
            path = f'/api/v1/space/{space_id}/content'
        else:
            path = '/api/v1/post'
        return await self._request('POST', path, json={'message': message})

    async def list_spaces(self) -> Any:
        return await self._request('GET', '/api/v1/space')

    async def sync_user(self, username: str, email: str, profile: dict[str, Any] | None = None) -> Any:
        payload = {'account': {'username': username, 'email': email}, 'profile': profile or {}}
        return await self._request('POST', '/api/v1/user', json=payload)

    async def list_dm_threads(self) -> Any:
        return await self._request('GET', '/api/v1/mail/message')

    async def read_dm_thread(self, thread_id: int) -> Any:
        return await self._request('GET', f'/api/v1/mail/message/{thread_id}')

    async def send_dm(self, message: str, thread_id: int | None = None, recipient_user_ids: list[int] | None = None) -> Any:
        payload: dict[str, Any] = {'message': message}
        if thread_id is not None:
            return await self._request('POST', f'/api/v1/mail/message/{thread_id}', json=payload)
        payload['recipientUserIds'] = recipient_user_ids or []
        return await self._request('POST', '/api/v1/mail/message', json=payload)
