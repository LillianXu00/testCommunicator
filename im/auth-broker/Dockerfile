FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir fastapi==0.116.1 uvicorn[standard]==0.35.0 PyJWT==2.10.1

COPY app ./app
COPY README.md ./README.md

EXPOSE 8080
ENTRYPOINT ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${AUTH_BROKER_INTERNAL_PORT:-8080}"]
