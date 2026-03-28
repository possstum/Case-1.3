from pathlib import Path
import sys

import jwt
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.main import create_app
from app.rate_limit import TokenBucketRateLimiter


class DummyQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object]]] = []

    def enqueue(self, job_id: str, payload: dict[str, object]) -> str:
        self.enqueued.append((job_id, payload))
        return job_id

    def healthcheck(self) -> bool:
        return True


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        queue_mode="inline",
        api_keys=["test-api-key"],
        jwt_secret="test-jwt-secret-1234567890-abcdef",
        rate_limit_capacity=50,
        rate_limit_refill_per_second=50.0,
        websocket_poll_interval_seconds=0.01,
    )


@pytest.fixture()
def queue_backend() -> DummyQueue:
    return DummyQueue()


@pytest.fixture()
def app(test_settings: Settings, queue_backend: DummyQueue):
    limiter = TokenBucketRateLimiter(capacity=50, refill_per_second=50.0)
    return create_app(
        settings=test_settings,
        queue_backend=queue_backend,
        rate_limiter=limiter,
    )


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    token = jwt.encode(
        {"sub": "unit-test-user"},
        "test-jwt-secret-1234567890-abcdef",
        algorithm="HS256",
    )
    return {
        "X-API-Key": "test-api-key",
        "Authorization": f"Bearer {token}",
    }
