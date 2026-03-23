import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from .auth import require_bearer_token
from .config import get_settings
from .dependencies import get_humhub_client, get_user_mapping_service
from .exceptions import AdapterError
from .logging_config import setup_logging
from .models import ApiMessage, DmSendRequest, FeedPostRequest, UserSyncRequest

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info('adapter_starting')
    yield
    logger.info('adapter_stopping')


app = FastAPI(title='im-adapter', version='0.1.0', lifespan=lifespan)


@app.exception_handler(AdapterError)
async def adapter_error_handler(_: Request, exc: AdapterError):
    return JSONResponse(status_code=exc.status_code, content={'status': 'error', 'error_code': exc.error_code, 'detail': exc.message})


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/v1/users/sync', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def sync_user(payload: UserSyncRequest):
    mapper = get_user_mapping_service()
    mapper.resolve_actor(payload.external_id)
    profile = {'displayName': payload.display_name or payload.username}
    result = await get_humhub_client().sync_user(payload.username, payload.email, profile)
    return ApiMessage(detail='user synced', data=result)


@app.get('/v1/feed', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def list_feed(limit: int = 20):
    result = await get_humhub_client().list_feed(limit=limit)
    return ApiMessage(detail='feed fetched', data=result)


@app.post('/v1/feed/post', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def post_feed(payload: FeedPostRequest):
    result = await get_humhub_client().post_feed(payload.message, payload.space_id)
    return ApiMessage(detail='feed post created', data=result)


@app.get('/v1/spaces', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def list_spaces():
    result = await get_humhub_client().list_spaces()
    return ApiMessage(detail='spaces fetched', data=result)


@app.post('/v1/spaces/{space_id}/post', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def post_space_feed(space_id: int, payload: FeedPostRequest):
    result = await get_humhub_client().post_feed(payload.message, space_id)
    return ApiMessage(detail='space post created', data=result)


@app.post('/v1/dm/send', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def send_dm(payload: DmSendRequest):
    result = await get_humhub_client().send_dm(payload.message, payload.thread_id, payload.recipient_user_ids)
    return ApiMessage(detail='dm sent', data=result)


@app.get('/v1/dm/threads', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def list_dm_threads():
    result = await get_humhub_client().list_dm_threads()
    return ApiMessage(detail='dm threads fetched', data=result)


@app.get('/v1/dm/threads/{thread_id}', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def read_dm_thread(thread_id: int):
    result = await get_humhub_client().read_dm_thread(thread_id)
    return ApiMessage(detail='dm thread fetched', data=result)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.main:app', host='0.0.0.0', port=8000)
