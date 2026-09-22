FROM python:3.11-slim

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir "a2a-sdk[http-server]>=1.0.3,<2" \
    "aio-pika>=9.5,<10" \
    "httpx>=0.28.1,<1" \
    "starlette>=0.46.2,<1" \
    "uvicorn>=0.34.2,<1"

COPY a2a_server/ a2a_server/

ENV A2A_HOST=0.0.0.0
ENV A2A_PORT=4101

EXPOSE 4101

CMD ["uvicorn", "a2a_server.server:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "4101"]
