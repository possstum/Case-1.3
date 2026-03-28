import threading
from typing import Protocol

from redis import Redis
from rq import Queue

from .config import Settings
from .tasks import run_generation_job


class QueueBackend(Protocol):
    def enqueue(self, job_id: str, payload: dict[str, object]) -> str:
        ...

    def healthcheck(self) -> bool:
        ...


class RQQueueBackend:
    def __init__(
        self,
        redis_url: str,
        queue_name: str,
        *,
        job_timeout_seconds: int,
        result_ttl_seconds: int,
    ) -> None:
        self.redis = Redis.from_url(
            redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=False,
        )
        self.queue = Queue(name=queue_name, connection=self.redis)
        self.job_timeout_seconds = job_timeout_seconds
        self.result_ttl_seconds = result_ttl_seconds

    def enqueue(self, job_id: str, payload: dict[str, object]) -> str:
        job = self.queue.enqueue(
            "worker.run_generation_job",
            job_id,
            payload,
            job_id=job_id,
            result_ttl=self.result_ttl_seconds,
            job_timeout=self.job_timeout_seconds,
        )
        return job.id

    def healthcheck(self) -> bool:
        return bool(self.redis.ping())


class InlineQueueBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def enqueue(self, job_id: str, payload: dict[str, object]) -> str:
        worker = threading.Thread(
            target=run_generation_job,
            args=(job_id, payload, self.settings),
            daemon=True,
            name=f"lyrics2song-inline-{job_id}",
        )
        worker.start()
        return job_id

    def healthcheck(self) -> bool:
        return True


def build_queue_backend(settings: Settings) -> QueueBackend:
    if settings.queue_mode == "inline":
        return InlineQueueBackend(settings)
    return RQQueueBackend(
        settings.redis_url,
        settings.queue_name,
        job_timeout_seconds=settings.queue_job_timeout_seconds,
        result_ttl_seconds=settings.queue_result_ttl_seconds,
    )
