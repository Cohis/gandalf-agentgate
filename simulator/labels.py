"""Deliver the answer key to the engine, the way chargebacks and reviews arrive: late.

uv run python -m simulator.labels data/runs/<run>.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from .common import guard

ENGINE_URL = os.environ.get("LAB_ENGINE_URL", "http://127.0.0.1:8000")
KEY = os.environ.get("GANDALF_SERVICE_KEY", "dev-only-change-me")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    rows = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
    labels = [
        {
            "avaliacao_id": r["avaliacao_id"],
            "eh_automacao": r["eh_automacao"],
            "eh_fraude": r["eh_fraude"],
            "ator_real": r["ator_real"],
            "nivel_atacante": r["nivel"],
            "perfil": r["perfil"],
            "fonte": f"{r['driver']}:{r['cenario']}",
        }
        for r in rows
    ]
    sent = 0
    with httpx.Client(base_url=guard(ENGINE_URL), headers={"X-Gandalf-Key": KEY}, timeout=30) as c:
        for i in range(0, len(labels), 2000):
            r = c.post("/rotulos", json={"rotulos": labels[i : i + 2000]})
            r.raise_for_status()
            sent += r.json()["recebidos"]
    print(f"{sent} rótulos entregues ao motor")


if __name__ == "__main__":
    main()
