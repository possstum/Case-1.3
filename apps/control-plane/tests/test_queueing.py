from types import SimpleNamespace

from app.config import Settings
from app.queueing import InlineQueueBackend, RQQueueBackend, build_queue_backend


def test_inline_queue_backend_starts_background_thread(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyThread:
        def __init__(self, *, target, args, daemon, name) -> None:
            captured["target"] = target
            captured["args"] = args
            captured["daemon"] = daemon
            captured["name"] = name

        def start(self) -> None:
            captured["started"] = True

    from app import queueing

    monkeypatch.setattr(queueing.threading, "Thread", DummyThread)
    backend = InlineQueueBackend(Settings(queue_mode="inline"))

    queue_job_id = backend.enqueue("job-1", {"lyrics": "la la la"})

    assert queue_job_id == "job-1"
    assert captured["args"] == ("job-1", {"lyrics": "la la la"}, backend.settings)
    assert captured["daemon"] is True
    assert captured["name"] == "lyrics2song-inline-job-1"
    assert captured["started"] is True


def test_rq_queue_backend_enqueues_with_expected_rq_options(monkeypatch) -> None:
    queue_calls: dict[str, object] = {}

    class DummyRedis:
        def ping(self) -> bool:
            return True

    class DummyQueue:
        def __init__(self, name, connection) -> None:
            queue_calls["name"] = name
            queue_calls["connection"] = connection

        def enqueue(self, func_name, *args, **kwargs):
            queue_calls["func_name"] = func_name
            queue_calls["args"] = args
            queue_calls["kwargs"] = kwargs
            return SimpleNamespace(id=f"rq-{args[0]}")

    from app import queueing

    monkeypatch.setattr(
        queueing.Redis,
        "from_url",
        lambda *args, **kwargs: DummyRedis(),
    )
    monkeypatch.setattr(queueing, "Queue", DummyQueue)

    backend = RQQueueBackend(
        "redis://example.com/0",
        "lyrics2song-jobs",
        job_timeout_seconds=7200,
        result_ttl_seconds=86400,
    )

    queue_job_id = backend.enqueue("job-2", {"lyrics": "la la la"})

    assert queue_job_id == "rq-job-2"
    assert queue_calls["name"] == "lyrics2song-jobs"
    assert queue_calls["func_name"] == "worker.run_generation_job"
    assert queue_calls["args"] == ("job-2", {"lyrics": "la la la"})
    assert queue_calls["kwargs"]["job_timeout"] == 7200
    assert queue_calls["kwargs"]["result_ttl"] == 86400
    assert backend.healthcheck() is True


def test_build_queue_backend_returns_inline_backend() -> None:
    backend = build_queue_backend(Settings(queue_mode="inline"))

    assert isinstance(backend, InlineQueueBackend)
