"""Redis side of the sliding windows. Must stay semantically identical to
``gandalf_features.MemoryStateStore`` (tests/parity enforces it).

The idempotency lookup and record ride in the same pipelines as the windows, so a
request costs exactly two Redis round trips: one read, one write.
"""

from __future__ import annotations

from gandalf_features import MAX_WINDOW_S, StateWrite, WindowQuery
from redis.asyncio import Redis

_TTL_S = MAX_WINDOW_S + 60
_IDEMPOTENCY_TTL_S = 86_400


class RedisStateStore:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def read(
        self, queries: list[WindowQuery], now: float, idem_key: str | None = None
    ) -> tuple[bytes | None, dict[str, int]]:
        """Cached response (if this request was already answered) plus all window
        counts, in a single round trip. Window is (now - W, now]."""
        if not queries and not idem_key:
            return None, {}
        pipe = self._r.pipeline(transaction=False)
        if idem_key:
            pipe.get(idem_key)
        for q in queries:
            pipe.zcount(q.key, f"({now - q.window_s!r}", repr(now))
        out = await pipe.execute()
        cached = out.pop(0) if idem_key else None
        return cached, {q.name: int(c) for q, c in zip(queries, out, strict=True)}

    async def write(
        self,
        writes: list[StateWrite],
        now: float,
        idem_key: str | None = None,
        response_json: str | None = None,
    ) -> None:
        if not writes and not idem_key:
            return
        pipe = self._r.pipeline(transaction=False)
        for w in writes:
            pipe.zadd(w.key, {w.member: now})
            pipe.zremrangebyscore(w.key, "-inf", repr(now - MAX_WINDOW_S))
            pipe.expire(w.key, _TTL_S)
        if idem_key and response_json is not None:
            pipe.set(idem_key, response_json, ex=_IDEMPOTENCY_TTL_S, nx=True)
        await pipe.execute()
