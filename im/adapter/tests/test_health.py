import os
from fastapi.testclient import TestClient

os.environ.setdefault('ADAPTER_BEARER_TOKEN', 'test-token')
os.environ.setdefault('HUMHUB_SERVICE_ACCOUNT_TOKEN', 'service-token')

from app.main import app  # noqa: E402


def test_healthz():
    client = TestClient(app)
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
