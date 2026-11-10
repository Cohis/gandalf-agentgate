"""Redis side of the sliding windows. Must stay semantically identical to
``gandalf_features.MemoryStateStore`` (tests/parity enforces it)."""

from __future__ import annotations

from gandalf_features import MAX_WINDOW_S, StateWrite, WindowQuery
from redis.asyncio import Redis

_TTL_S = MAX_WINDOW_S + 60


class RedisStateStore:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def read(self, queries: list[WindowQuery], now: float) -> dict[str, int]:
        """All window counts in a single round trip. Window is (now - W, now]."""
        if not queries:
            return {}
        pipe = self._r.pipeline(transaction=False)
        for q in queries:
            pipe.zcount(q.key, f"({now - q.window_s!r}", repr(now))
        counts = await pipe.execute()
        return {q.name: int(c) for q, c in zip(queries, counts, strict=True)}

    async def write(self, writes: list[StateWrite], now: float) -> None:
        if not writes:
            return
        pipe = self._r.pipeline(transaction=False)
        for w in writes:
            pipe.zadd(w.key, {w.member: now})
            pipe.zremrangebyscore(w.key, "-inf", repr(now - MAX_WINDOW_S))
            pipe.expire(w.key, _TTL_S)
        await pipe.execute()
