import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import require_bearer_token
from .config import get_settings
from .dependencies import get_humhub_client
from .exceptions import AdapterError
from .logging_config import setup_logging
from .models import ApiMessage, DmSendRequest, FeedPostRequest
from .routers.users import router as users_router

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info('adapter_starting')
    yield
    logger.info('adapter_stopping')


app = FastAPI(title='im-adapter', version='0.2.0', lifespan=lifespan)
app.include_router(users_router)


@app.exception_handler(AdapterError)
async def adapter_error_handler(_: Request, exc: AdapterError):
    return JSONResponse(status_code=exc.status_code, content={'status': 'error', 'error_code': exc.error_code, 'detail': exc.message})


@app.get('/healthz')
async def healthz() -> dict[str, str]:
    return {'status': 'ok'}


@app.get('/v1/feed', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def list_feed(limit: int = 20):
    result = await get_humhub_client().list_feed(limit=limit)
    return ApiMessage(detail='feed fetched', data=result)


@app.post('/v1/feed/post', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def post_feed(payload: FeedPostRequest):
    result = await get_humhub_client().post_feed(payload.message, payload.space_id, payload.created_by)
    note = 'production recommendation: always sync-user first and pass created_by=mapped_humhub_user_id'
    return ApiMessage(detail=note, data=result)


@app.get('/v1/spaces', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def list_spaces():
    result = await get_humhub_client().list_spaces()
    return ApiMessage(detail='spaces fetched', data=result)


@app.post('/v1/spaces/{space_id}/post', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def post_space_feed(space_id: int, payload: FeedPostRequest):
    result = await get_humhub_client().post_feed(payload.message, space_id, payload.created_by)
    note = 'production recommendation: always sync-user first and pass created_by=mapped_humhub_user_id'
    return ApiMessage(detail=note, data=result)


@app.post('/v1/dm/send', dependencies=[Depends(require_bearer_token)], response_model=ApiMessage)
async def send_dm(payload: DmSendRequest):
    result = await get_humhub_client().send_dm(
        payload.message,
        thread_id=payload.thread_id,
        recipient_user_ids=payload.recipient_user_ids,
        sender_user_id=payload.sender_user_id,
    )
    note = 'production recommendation: always sync-user first and pass sender_user_id=mapped_humhub_user_id'
    return ApiMessage(detail=note, data=result)


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
