from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from fastapi.testclient import TestClient
from gandalf_engine.app import create_app
from gandalf_engine.config import Settings
from gandalf_engine.storage import NullStorage

KEY = "test-key"
T0 = datetime(2026, 11, 10, 14, 0, 0, tzinfo=UTC)

HUMAN_TELEMETRY: dict[str, Any] = {
    "coletada": True,
    "intervalos_acao_ms": [820, 2400, 310, 5600, 1210, 940, 3900, 450],
    "trajetoria_cursor_linear": 0.62,
    "cliques_no_centro": 0.2,
    "digitacao": "humana",
    "webdriver": False,
    "inconsistencias_fingerprint": 0,
    "honeypot_preenchido": False,
    "canario_detectado": False,
}

_seq = 0


def make_event(**over: Any) -> dict[str, Any]:
    """A plausible human login. Override any field; ``offset_s`` shifts the event time."""
    global _seq
    _seq += 1
    offset = over.pop("offset_s", 0)
    ev: dict[str, Any] = {
        "id_requisicao": f"req_{_seq}",
        "evento": "login",
        "momento": (T0 + timedelta(seconds=offset)).isoformat(),
        "conta_id": "c_1",
        "dispositivo_id": "d_1",
        "sessao_id": "s_1",
        "ip": "203.0.113.7",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/142.0 Safari/537.36",
        "ja4": "t13d1516h2_8daaf6152771_02713d6af862",
        "telemetria": dict(HUMAN_TELEMETRY),
        "sessao": {
            "paginas_vistas": 6,
            "segundos_desde_inicio": 95.0,
            "desvios_do_caminho": 2,
            "voltas": 1,
        },
    }
    ev.update(over)
    return ev


class Harness:
    def __init__(self, client: TestClient, storage: NullStorage, redis: FakeAsyncRedis) -> None:
        self.client = client
        self.storage = storage
        self.redis = redis

    def assess(self, **over: Any) -> dict[str, Any]:
        r = self.client.post("/avaliar", json=make_event(**over), headers={"X-Gandalf-Key": KEY})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def harness() -> Iterator[Harness]:
    storage = NullStorage()
    redis = FakeAsyncRedis()
    app = create_app(Settings(service_key=KEY), redis=redis, storage=storage)
    with TestClient(app) as client:
        yield Harness(client, storage, redis)
