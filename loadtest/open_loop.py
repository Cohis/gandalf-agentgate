"""Open-loop load generator: fixed arrival rate, evenly spaced, whatever the latency.

    uv run python loadtest/open_loop.py --rps 200 --seconds 30

Reports client-side latency and the engine's own measurement side by side, so
queueing in the client is never mistaken for engine latency.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

KEY = os.environ.get("GANDALF_SERVICE_KEY", "dev-only-change-me")
ALLOWED = {"127.0.0.1", "localhost", "::1", "engine"}


def body() -> dict[str, object]:
    n = random.randrange(5000)
    return {
        "id_requisicao": uuid.uuid4().hex,
        "evento": random.choice(["cadastro", "login", "checkout", "adicionar_carrinho"]),
        "momento": datetime.now(UTC).isoformat(),
        "conta_id": f"c_{n}",
        "dispositivo_id": f"d_{n % 3000}",
        "sessao_id": f"s_{n}",
        "ip": f"203.0.{n % 250}.{n % 199 + 1}",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/142.0 Safari/537.36",
        "ja4": "t13d1516h2_8daaf6152771_02713d6af862",
        "telemetria": {
            "intervalos_acao_ms": [random.randint(200, 6000) for _ in range(12)],
            "trajetoria_cursor_linear": random.random(),
            "cliques_no_centro": random.random() / 2,
            "digitacao": "humana",
        },
    }


def pct(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8010")
    ap.add_argument("--rps", type=int, default=50)
    ap.add_argument("--seconds", type=int, default=30)
    a = ap.parse_args()
    if urlparse(a.host).hostname not in ALLOWED:
        raise SystemExit("refusing: only the local lab may be load-tested")

    client_ms: list[float] = []
    engine_ms: list[float] = []
    errors = 0
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    async with httpx.AsyncClient(
        base_url=a.host, headers={"X-Gandalf-Key": KEY}, limits=limits, timeout=5
    ) as c:

        async def fire() -> None:
            nonlocal errors
            t = time.perf_counter()
            try:
                r = await c.post("/avaliar", json=body())
                r.raise_for_status()
                engine_ms.append(r.json()["latencia_ms"])
                client_ms.append((time.perf_counter() - t) * 1000)
            except httpx.HTTPError:
                errors += 1

        total = a.rps * a.seconds
        start = time.perf_counter()
        tasks = []
        for i in range(total):
            delay = start + i / a.rps - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(fire()))
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

    client_ms.sort()
    engine_ms.sort()
    print(f"{len(client_ms)} ok, {errors} errors, {len(client_ms) / elapsed:.0f} req/s achieved")
    for name, xs in (("engine (self-measured)", engine_ms), ("client (end to end)", client_ms)):
        p50, p95, p99 = (pct(xs, q) for q in (0.5, 0.95, 0.99))
        print(f"  {name:24s} p50={p50:6.2f}  p95={p95:6.2f}  p99={p99:6.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())
