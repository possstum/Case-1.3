from redis import Redis
from rq import Worker

from .config import Settings


def main() -> int:
    settings = Settings()
    redis = Redis.from_url(settings.redis_url)
    worker = Worker([settings.queue_name], connection=redis)
    worker.work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

