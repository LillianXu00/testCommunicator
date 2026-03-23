import os
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
import jwt

app = FastAPI(title='auth-broker', version='0.1.0')


def _required(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'missing environment variable: {name}')
    return value


@app.get('/healthz')
def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/login')
def login(username: str):
    if not username:
        raise HTTPException(status_code=400, detail='username is required')

    secret = _required('JWT_SSO_SHARED_SECRET')
    issuer = os.environ.get('JWT_SSO_ISSUER', 'im-auth-broker')
    audience = os.environ.get('JWT_SSO_AUDIENCE', 'humhub')
    base_url = os.environ.get('PUBLIC_BASE_URL', 'http://localhost').rstrip('/')
    redirect_path = os.environ.get('JWT_SSO_ADMIN_REDIRECT_PATH', '/')

    now = int(time.time())
    payload = {
        'sub': username,
        'email': username,
        'iss': issuer,
        'aud': audience,
        'iat': now,
        'exp': now + 300,
    }
    token = jwt.encode(payload, secret, algorithm='HS256')
    target = f"{base_url}/user/auth/external?token={token}&redirect={redirect_path}"
    return RedirectResponse(url=target, status_code=302)
