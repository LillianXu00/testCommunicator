from fastapi import Header
from .config import get_settings
from .exceptions import UnauthorizedError


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().adapter_bearer_token
    if not authorization or not authorization.startswith('Bearer '):
        raise UnauthorizedError('missing bearer token')
    provided = authorization.split(' ', 1)[1].strip()
    if provided != expected:
        raise UnauthorizedError('invalid bearer token')
