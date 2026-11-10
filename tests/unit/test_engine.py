from __future__ import annotations

from pathlib import Path

import pytest
from gandalf_engine.config import Settings
from gandalf_engine.rules import FORBIDDEN_IN_RULES, load_rules
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.conftest import KEY, Harness, make_event


def test_requires_service_key(harness: Harness) -> None:
    assert harness.client.post("/avaliar", json=make_event()).status_code == 401
    r = harness.client.post("/avaliar", json=make_event(), headers={"X-Gandalf-Key": "nope"})
    assert r.status_code == 401


def test_ordinary_human_is_approved(harness: Harness) -> None:
    out = harness.assess()
    assert out["decisao"] == "aprovar" and out["acao"] == "nenhuma"
    assert out["ator"]["tipo"] == "humano"
    assert out["motivos"] == [] and out["regras_acionadas"] == []
    assert out["versao_features"] == "f1" and out["modo_degradado"] is False


def test_raw_http_client_is_not_approved_and_says_why(harness: Harness) -> None:
    out = harness.assess(
        user_agent="python-requests/2.32", telemetria={"coletada": False}, ja4=None
    )
    assert out["decisao"] in {"desafiar", "negar"}
    assert out["ator"]["tipo"] == "automacao_nao_declarada"
    assert len(out["motivos"]) >= 2


def test_webdriver_is_challenged_with_passkey_not_denied(harness: Harness) -> None:
    tel = {**make_event()["telemetria"], "webdriver": True}
    out = harness.assess(telemetria=tel)
    assert out["decisao"] == "desafiar" and out["acao"] == "exigir_passkey"
    assert "webdriver_declarado" in out["regras_acionadas"]
    assert out["desafio_id"].startswith("ds_")


def test_trap_alone_never_blocks(harness: Harness) -> None:
    """A screen-reader user who filled the hidden field must still get through."""
    tel = {**make_event()["telemetria"], "honeypot_preenchido": True, "canario_detectado": True}
    out = harness.assess(telemetria=tel)
    assert out["decisao"] == "aprovar"


def test_shipped_rules_never_use_trap_signals() -> None:
    for rule in load_rules(Settings().rules_path):
        assert not {c.feature for c in rule.todas} & FORBIDDEN_IN_RULES


def test_loader_refuses_a_rule_built_on_a_trap(tmp_path: Path) -> None:
    bad = tmp_path / "rules.yaml"
    bad.write_text(
        "- id: x\n  decisao: negar\n  motivo: m\n"
        "  todas:\n    - {feature: honeypot, op: '==', valor: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="trap signal"):
        load_rules(bad)


def test_account_farm_on_one_device_gets_challenged(harness: Harness) -> None:
    decisions = [
        harness.assess(evento="cadastro", conta_id=f"c_farm_{i}", offset_s=i * 240)["decisao"]
        for i in range(10)
    ]
    assert decisions[0] == "aprovar"
    assert decisions[-1] == "desafiar"


def test_absurd_rate_from_one_ip_is_denied(harness: Harness) -> None:
    last = {}
    for i in range(302):
        last = harness.assess(dispositivo_id=f"d_{i}", conta_id=f"c_{i}", offset_s=i)
    assert last["decisao"] == "negar"
    assert "taxa_absurda_ip" in last["regras_acionadas"]


def test_idempotent_on_id_requisicao(harness: Harness) -> None:
    ev = make_event()
    h = {"X-Gandalf-Key": KEY}
    a = harness.client.post("/avaliar", json=ev, headers=h).json()
    b = harness.client.post("/avaliar", json=ev, headers=h).json()
    assert a == b
    assert len(harness.storage.records) == 1  # the retry was not counted twice


def test_redis_down_degrades_instead_of_failing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(harness.redis, "pipeline", boom)
    out = harness.assess()
    assert out["modo_degradado"] is True
    assert out["decisao"] == "aprovar"
    assert any("degradado" in m for m in out["motivos"])


def test_persisted_record_holds_no_raw_ip_or_signup_text(harness: Harness) -> None:
    harness.assess(
        evento="cadastro",
        ip="203.0.113.77",
        dados_cadastro={"nome": "Maria Souza", "endereco": "Rua A, 1", "bio": "oi"},
    )
    blob = repr(harness.storage.records[0])
    assert "203.0.113.77" not in blob
    assert "Maria Souza" not in blob and "Rua A" not in blob
    assert harness.storage.records[0].event["ip_prefixo"] == "203.0.113.0/24"


def test_labels_are_accepted_and_joined_by_avaliacao_id(harness: Harness) -> None:
    av = harness.assess()["avaliacao_id"]
    body = {
        "rotulos": [
            {"avaliacao_id": av, "eh_automacao": False, "eh_fraude": False, "ator_real": "humano"}
        ]
    }
    r = harness.client.post("/rotulos", json=body, headers={"X-Gandalf-Key": KEY})
    assert r.status_code == 200 and r.json() == {"recebidos": 1}
    assert harness.storage.labels[0].avaliacao_id == av


def test_health_and_metrics(harness: Harness) -> None:
    harness.assess()
    assert harness.client.get("/saude").json()["ok"] is True
    m = harness.client.get("/metricas", headers={"X-Gandalf-Key": KEY}).text
    assert 'gandalf_decisions_total{decisao="aprovar"} 1' in m
