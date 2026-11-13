"""Online == offline, checked two ways.

1. Store parity: Redis and the in-memory store return identical window counts.
2. Replay parity: replaying the request log through the same ``compute`` with the
   in-memory store reproduces, bit for bit, every vector the engine logged.
"""

from __future__ import annotations

import random

from fakeredis import FakeAsyncRedis
from gandalf_contracts import AssessRequest
from gandalf_engine.state import RedisStateStore
from gandalf_features import (
    FEATURE_NAMES,
    MemoryStateStore,
    NetworkInfo,
    ScaleWindow,
    compute,
    signup_scale_inputs,
    state_queries,
    state_writes,
)

from tests.conftest import KEY, Harness, make_event


def _random_events(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    t = 0.0
    out = []
    for _ in range(n):
        # mix of bursts and long gaps so every window (10 min, 1 h, 24 h) rolls over
        t += rng.choice([0.0, 0.5, 3, 40, 700, 4000, 30_000])
        evento = rng.choice(["cadastro", "login", "checkout", "adicionar_carrinho"])
        out.append(
            make_event(
                offset_s=t,
                evento=evento,
                conta_id=rng.choice([None, "c_1", "c_2", "c_3"]),
                dispositivo_id=rng.choice(["d_1", "d_2"]),
                ip=rng.choice(["203.0.113.7", "203.0.113.8", "2001:db8::1", "2001:db8::2"]),
                cartao_hash=rng.choice(["k_1", "k_2"]) if evento == "checkout" else None,
                dados_cadastro=(
                    {
                        "nome": "x",
                        "email": rng.choice(["ana.lima1@gmail.com", "zz9@uol.com.br", ""]),
                        "bio": rng.choice(
                            ["", "adoro viajar e cozinhar", "Amo viajar e cozinhar!"]
                        ),
                        "endereco": rng.choice(["", "Rua A, 12", "Av. Brasil 900"]),
                    }
                    if evento == "cadastro"
                    else None
                ),
            )
        )
    return out


async def test_redis_and_memory_stores_agree() -> None:
    redis_store, memory = RedisStateStore(FakeAsyncRedis()), MemoryStateStore()
    for i, raw in enumerate(_random_events(400, seed=7)):
        req = AssessRequest.model_validate(raw)
        now = req.momento.timestamp()
        q = state_queries(req)
        assert (await redis_store.read(q, now))[1] == memory.read(q, now), f"diverged at event {i}"
        w = state_writes(req, f"av_{i}")
        await redis_store.write(w, now)
        memory.write(w, now)


def test_offline_replay_reproduces_logged_vectors(harness: Harness) -> None:
    events = _random_events(300, seed=11)
    for raw in events:
        r = harness.client.post("/avaliar", json=raw, headers={"X-Gandalf-Key": KEY})
        assert r.status_code == 200, r.text

    logged = harness.storage.records
    assert len(logged) == len(events)

    memory, scale = MemoryStateStore(), ScaleWindow()
    for raw, record in zip(events, logged, strict=True):
        req = AssessRequest.model_validate(raw)
        now = req.momento.timestamp()
        vec, shape = signup_scale_inputs(req)
        signup = req.evento == "cadastro"
        info = scale.query(vec, shape, now) if signup else None
        replayed = compute(req, memory.read(state_queries(req), now), NetworkInfo(), info)
        if signup:
            scale.add(vec, shape, now)
        assert tuple(replayed) == FEATURE_NAMES
        assert replayed == record.features["features"]
        memory.write(state_writes(req, record.event["avaliacao_id"]), now)
