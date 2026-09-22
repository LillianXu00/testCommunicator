FROM python:3.11-slim

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir "a2a-sdk[http-server]>=1.0.3,<2" \
    "cryptography>=44,<50" \
    "httpx>=0.28.1,<1" \
    "starlette>=0.46.2,<1" \
    "uvicorn>=0.34.2,<1"

COPY registry_service/ registry_service/

ENV REGISTRY_HOST=0.0.0.0
ENV REGISTRY_PORT=4200

VOLUME ["/data"]
ENV REGISTRY_DB=/data/agent-registry.sqlite3

EXPOSE 4200

CMD ["uvicorn", "registry_service.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "4200"]
