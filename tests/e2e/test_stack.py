"""End-to-end checks against the running compose stack.

docker compose up -d --wait && make e2e
"""

from __future__ import annotations

import time
import uuid

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.e2e

SHOP = "https://localhost:8443"
DSN = "postgresql://gandalf:gandalf@localhost:5432/gandalf"


def _latest_event(since: float) -> dict:
    """Persistence is asynchronous: poll briefly for the row."""
    deadline = time.time() + 5
    while time.time() < deadline:
        with psycopg.connect(DSN) as conn:
            row = conn.execute(
                "SELECT e.avaliacao_id, e.ja4, e.ip_prefixo, e.evento, d.decisao, "
                "(SELECT count(*) FROM feature_snapshots f WHERE f.avaliacao_id = e.avaliacao_id) "
                "FROM events e JOIN decisions d USING (avaliacao_id) "
                "WHERE e.recebido_em > to_timestamp(%s) ORDER BY e.recebido_em DESC LIMIT 1",
                (since,),
            ).fetchone()
        if row:
            keys = ("avaliacao_id", "ja4", "ip_prefixo", "evento", "decisao", "snapshots")
            return dict(zip(keys, row, strict=True))
        time.sleep(0.2)
    raise AssertionError("no event was persisted")


def _signup(headers: dict[str, str] | None = None) -> httpx.Response:
    with httpx.Client(base_url=SHOP, verify=False) as c:  # self-signed lab certificate
        return c.post(
            "/cadastro",
            data={"nome": "E2E", "email": f"{uuid.uuid4().hex}@example.com", "senha": "segredo1"},
            headers=headers or {},
        )


def test_signup_is_persisted_with_ja4_and_feature_snapshot() -> None:
    since = time.time() - 1
    assert _signup().status_code in {303, 401, 403}
    ev = _latest_event(since)
    assert ev["evento"] == "cadastro"
    assert ev["ja4"], "the proxy must inject X-JA4-Fingerprint"
    assert ev["snapshots"] == 1


def test_forged_forwarded_for_does_not_change_the_recorded_ip() -> None:
    since = time.time() - 1
    _signup({"X-Forwarded-For": "8.8.8.8", "X-JA4-Fingerprint": "forged"})
    ev = _latest_event(since)
    assert ev["ip_prefixo"] != "8.8.8.0/24"
    assert ev["ja4"] != "forged"
