import time

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.models import JobStatus
from app.rate_limit import TokenBucketRateLimiter
from app.repository import replace_job_artifacts, update_job_state


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_serves_web_ui(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Lyrics2Song (YuE backend)" in response.text


def test_create_and_fetch_job(
    client: TestClient,
    app,
    auth_headers: dict[str, str],
    queue_backend,
) -> None:
    create_response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "tags": ["synthpop", "female vocal"],
            "duration": 60,
            "model_name": "lyrics2song-demo-v1",
        },
    )
    assert create_response.status_code == 202
    body = create_response.json()
    assert body["status"] == JobStatus.QUEUED.value
    assert body["progress"] == 0.0
    assert queue_backend.enqueued

    job_id = body["job_id"]
    get_response = client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
    assert get_response.status_code == 200
    assert get_response.json()["job_id"] == job_id
    assert get_response.json()["result"] is None

    with app.state.session_factory() as session:
        update_job_state(
            session,
            job_id=job_id,
            status=JobStatus.COMPLETED.value,
            progress=1.0,
            eta_seconds=0,
            result_url=f"https://files.example.com/{job_id}.mp3",
        )
        replace_job_artifacts(
            session,
            job_id=job_id,
            files={
                "mixed": f"https://files.example.com/{job_id}/mixed.mp3",
                "vocals": f"https://files.example.com/{job_id}/vocals.mp3",
                "instrumental": f"https://files.example.com/{job_id}/instrumental.mp3",
            },
        )

    status_response = client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["result"]["mixed_url"].startswith(
        f"https://files.example.com/{job_id}/mixed.mp3"
    )
    assert "signature=" not in status_body["result"]["mixed_url"]
    assert "signature=" not in status_body["result"]["vocals_url"]
    assert "signature=" not in status_body["result"]["instrumental_url"]
    assert "expires=" not in status_body["result"]["mixed_url"]
    assert "expires=" not in status_body["result"]["vocals_url"]
    assert "expires=" not in status_body["result"]["instrumental_url"]
    assert "expires_at" not in status_body["result"]

    result_response = client.get(
        f"/v1/jobs/{job_id}/result?redirect=false",
        headers=auth_headers,
    )
    assert result_response.status_code == 200
    result_body = result_response.json()
    assert result_body["job_id"] == job_id
    assert result_body["result_url"] == f"https://files.example.com/{job_id}/mixed.mp3"
    assert "signature=" not in result_body["result_url"]
    assert "expires=" not in result_body["result_url"]
    assert "signed_url" not in result_body
    assert "expires_at" not in result_body
    assert set(result_body["files"]) == {"mixed", "vocals", "instrumental"}
    assert "signature=" not in result_body["files"]["mixed"]
    assert "expires=" not in result_body["files"]["mixed"]


def test_create_job_accepts_genre_tags_and_segments(
    client: TestClient,
    auth_headers: dict[str, str],
    queue_backend,
) -> None:
    create_response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "genre_tags": "dream-pop, airy, female vocal",
            "segments": 2,
        },
    )

    assert create_response.status_code == 202
    _, payload = queue_backend.enqueued[-1]
    assert payload["tags"] == ["dream-pop", "airy", "female vocal"]
    assert payload["segments"] == 2
    assert payload["duration"] == 60


def test_create_job_rejects_unsupported_model(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "tags": ["synthpop"],
            "duration": 60,
            "model_name": "missing-model",
        },
    )

    assert response.status_code == 400
    assert "Unsupported model" in response.json()["detail"]


def test_create_job_returns_503_when_queue_enqueue_fails(
    test_settings,
    auth_headers: dict[str, str],
) -> None:
    class BrokenQueue:
        def enqueue(self, job_id: str, payload: dict[str, object]) -> str:
            _ = (job_id, payload)
            raise RuntimeError("redis unavailable")

        def healthcheck(self) -> bool:
            return False

    app = create_app(
        settings=test_settings,
        queue_backend=BrokenQueue(),
        rate_limiter=TokenBucketRateLimiter(
            capacity=50,
            refill_per_second=50.0,
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers=auth_headers,
            json={
                "lyrics": "la la la",
                "tags": ["synthpop"],
                "duration": 60,
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to enqueue job"


def test_inline_queue_completes_and_serves_demo_artifacts(
    test_settings,
    auth_headers: dict[str, str],
    tmp_path,
) -> None:
    inline_settings = test_settings.model_copy(
        update={
            "queue_mode": "inline",
            "simulated_job_step_seconds": 0.0,
            "demo_artifacts_dir": str(tmp_path / "artifacts"),
        }
    )
    app = create_app(settings=inline_settings)

    with TestClient(app) as client:
        create_response = client.post(
            "/v1/jobs",
            headers=auth_headers,
            json={
                "lyrics": "la la la",
                "genre_tags": "synthpop, airy vocal",
                "segments": 1,
            },
        )
        assert create_response.status_code == 202
        job_id = create_response.json()["job_id"]

        deadline = time.time() + 2.0
        final_response = None
        while time.time() < deadline:
            final_response = client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
            assert final_response.status_code == 200
            if final_response.json()["status"] == JobStatus.COMPLETED.value:
                break
            time.sleep(0.02)

        assert final_response is not None
        job_body = final_response.json()
        assert job_body["status"] == JobStatus.COMPLETED.value
        assert job_body["result"]["mixed_url"].startswith(f"/artifacts/{job_id}/mixed.wav")
        assert job_body["result"]["vocals_url"].startswith(f"/artifacts/{job_id}/vocals.wav")
        assert job_body["result"]["instrumental_url"].startswith(
            f"/artifacts/{job_id}/instrumental.wav"
        )

        mixed_response = client.get(job_body["result"]["mixed_url"])
        assert mixed_response.status_code == 200
        assert mixed_response.headers["content-type"].startswith("audio/wav")
        assert mixed_response.content[:4] == b"RIFF"


def test_result_endpoint_redirects_when_requested(
    client: TestClient,
    app,
    auth_headers: dict[str, str],
) -> None:
    create_response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "tags": ["alt-rock"],
            "duration": 90,
            "model_name": "lyrics2song-demo-v1",
        },
    )
    job_id = create_response.json()["job_id"]

    with app.state.session_factory() as session:
        update_job_state(
            session,
            job_id=job_id,
            status=JobStatus.COMPLETED.value,
            progress=1.0,
            eta_seconds=0,
            result_url=f"https://files.example.com/{job_id}.mp3",
        )

    response = client.get(
        f"/v1/jobs/{job_id}/result",
        headers=auth_headers,
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"].startswith(
        f"https://files.example.com/{job_id}.mp3"
    )


def test_result_endpoint_returns_409_when_job_is_not_ready(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "tags": ["alt-rock"],
            "duration": 90,
        },
    )
    job_id = create_response.json()["job_id"]

    response = client.get(
        f"/v1/jobs/{job_id}/result?redirect=false",
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Result is not ready"


def test_models_and_rate_limits(client: TestClient, auth_headers: dict[str, str]) -> None:
    models_response = client.get("/v1/models", headers=auth_headers)
    assert models_response.status_code == 200
    assert models_response.json()["models"][0]["id"] == "lyrics2song-demo-v1"

    rate_limit_response = client.get("/v1/rate-limits", headers=auth_headers)
    assert rate_limit_response.status_code == 200
    assert rate_limit_response.json()["capacity"] == 50


def test_auth_rejects_missing_api_key(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 401


def test_invalid_optional_jwt_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    headers = dict(auth_headers)
    headers["Authorization"] = "Bearer invalid-token"
    response = client.get("/v1/models", headers=headers)
    assert response.status_code == 401


def test_rate_limit_exhaustion_returns_429(test_settings, queue_backend) -> None:
    limited_settings = test_settings.model_copy(
        update={
            "rate_limit_capacity": 2,
            "rate_limit_refill_per_second": 0.0,
        }
    )
    app = create_app(
        settings=limited_settings,
        queue_backend=queue_backend,
        rate_limiter=TokenBucketRateLimiter(
            capacity=2,
            refill_per_second=0.0,
        ),
    )

    with TestClient(app) as client:
        headers = {"X-API-Key": "test-api-key"}
        assert client.get("/v1/models", headers=headers).status_code == 200
        assert client.get("/v1/models", headers=headers).status_code == 200
        third = client.get("/v1/models", headers=headers)
        assert third.status_code == 429


def test_websocket_progress_stream(
    client: TestClient,
    app,
    auth_headers: dict[str, str],
) -> None:
    create_response = client.post(
        "/v1/jobs",
        headers=auth_headers,
        json={
            "lyrics": "la la la",
            "tags": ["indietronica"],
            "duration": 45,
            "model_name": "lyrics2song-demo-v1",
        },
    )
    job_id = create_response.json()["job_id"]

    with app.state.session_factory() as session:
        update_job_state(
            session,
            job_id=job_id,
            status=JobStatus.RUNNING.value,
            progress=0.35,
            eta_seconds=60,
        )

    with client.websocket_connect(
        f"/v1/ws?job_id={job_id}",
        headers=auth_headers,
    ) as websocket:
        message = websocket.receive_json()
        assert message["job_id"] == job_id
        assert message["status"] == JobStatus.RUNNING.value
        assert message["progress"] == 0.35


def test_websocket_requires_job_id(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/v1/ws",
            headers=auth_headers,
        ):
            pass

    assert exc_info.value.code == 1008
