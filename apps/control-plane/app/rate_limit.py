from dataclasses import dataclass
import math
import threading
import time


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    capacity: int
    remaining_tokens: float
    refill_per_second: float
    retry_after_seconds: float


@dataclass(slots=True)
class TokenBucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _refill(self, bucket: TokenBucket, now: float) -> TokenBucket:
        elapsed = max(0.0, now - bucket.updated_at)
        tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
        return TokenBucket(tokens=tokens, updated_at=now)

    def peek(self, key: str) -> RateLimitDecision:
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(
                key, TokenBucket(tokens=float(self.capacity), updated_at=now)
            )
            bucket = self._refill(bucket, now)
            self._buckets[key] = bucket
            return RateLimitDecision(
                allowed=True,
                capacity=self.capacity,
                remaining_tokens=max(0.0, bucket.tokens),
                refill_per_second=self.refill_per_second,
                retry_after_seconds=0.0,
            )

    def consume(self, key: str, cost: float = 1.0) -> RateLimitDecision:
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(
                key, TokenBucket(tokens=float(self.capacity), updated_at=now)
            )
            bucket = self._refill(bucket, now)

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                self._buckets[key] = bucket
                return RateLimitDecision(
                    allowed=True,
                    capacity=self.capacity,
                    remaining_tokens=max(0.0, bucket.tokens),
                    refill_per_second=self.refill_per_second,
                    retry_after_seconds=0.0,
                )

            missing = cost - bucket.tokens
            retry_after = (
                math.ceil(missing / self.refill_per_second)
                if self.refill_per_second > 0
                else math.inf
            )
            self._buckets[key] = bucket
            return RateLimitDecision(
                allowed=False,
                capacity=self.capacity,
                remaining_tokens=max(0.0, bucket.tokens),
                refill_per_second=self.refill_per_second,
                retry_after_seconds=float(retry_after),
            )

