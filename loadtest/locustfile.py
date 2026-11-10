"""Burst scenario for POST /avaliar (worst case, not the headline number).

    make load-burst

Locust users with ``constant_throughput`` all fire in the same instant each
second, so this measures how long the single-worker engine takes to drain a burst
of N simultaneous requests. For steady-state latency use ``loadtest/open_loop.py``.

Refuses any host that is not local: this repository only ever attacks its own lab.
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from locust import FastHttpUser, constant_throughput, events, task

KEY = os.environ.get("GANDALF_SERVICE_KEY", "dev-only-change-me")
ALLOWED = {"127.0.0.1", "localhost", "::1", "engine"}


@events.init.add_listener
def only_the_lab(environment, **_: object) -> None:  # type: ignore[no-untyped-def]
    host = urlparse(environment.host or "").hostname
    if host not in ALLOWED:
        raise SystemExit(f"refusing to load-test '{host}': only the local lab is allowed")


class Shop(FastHttpUser):
    wait_time = constant_throughput(1)  # 1 request/s per user: users == target RPS

    @task
    def avaliar(self) -> None:
        n = random.randrange(5000)
        body = {
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
        self.client.post("/avaliar", json=body, headers={"X-Gandalf-Key": KEY})
