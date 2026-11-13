"""Phase 5: a declared agent with a valid mandate gets through; everything else
that claims to be one does not get a free pass."""

from __future__ import annotations

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gandalf_engine.identity import (
    issue_mandate,
    public_jwk,
    sign_request,
    thumbprint,
)

from tests.conftest import KEY, Harness, make_event

OPERATOR = "http://agent-operator:8090"
AUTHORITY = "localhost:8443"


@pytest.fixture
def keys(harness: Harness) -> dict[str, Any]:
    agent, other, shop = (Ed25519PrivateKey.generate() for _ in range(3))
    gate = harness.client.app.state.engine.agents  # type: ignore[attr-defined]
    gate.directory.keys[OPERATOR] = {
        thumbprint(public_jwk(k.public_key())): k.public_key() for k in (agent, other)
    }
    gate.issuers["loja-1"] = shop.public_key()
    return {"agent": agent, "other": other, "shop": shop}


def mandate(keys: dict[str, Any], **over: Any) -> str:
    claims = {
        "jti": "m_1",
        "sub": "c_1",
        "agente": thumbprint(public_jwk(keys["agent"].public_key())),
        "escopo": ["login", "adicionar_carrinho", "checkout"],
        "limite_valor": 100.0,
        "limite_acumulado": 150.0,
        "exp": int(time.time()) + 3600,
    } | over
    return issue_mandate(keys["shop"], "loja-1", claims)


def signed(
    keys: dict[str, Any], path: str = "/checkout", *, key: str = "agent", **over: Any
) -> dict:
    h = sign_request(keys[key], OPERATOR, method="POST", authority=AUTHORITY, path=path)
    sig = {
        "signature": h["Signature"],
        "signature_input": h["Signature-Input"],
        "signature_agent": h["Signature-Agent"],
        "metodo": "POST",
        "autoridade": AUTHORITY,
        "caminho": path,
    }
    # an agent sends no page telemetry: without a verified identity it looks like a bot
    return (
        make_event(
            evento="checkout", valor=49.9, telemetria={"coletada": False}, assinatura_agente=sig
        )
        | over
    )


def post(h: Harness, ev: dict) -> dict:
    r = h.client.post("/avaliar", json=ev, headers={"X-Gandalf-Key": KEY})
    assert r.status_code == 200, r.text
    return r.json()


def test_declared_agent_inside_its_mandate_is_approved(harness: Harness, keys) -> None:
    out = post(harness, signed(keys, mandato=mandate(keys)))
    assert out["decisao"] == "aprovar"
    assert out["ator"]["tipo"] == "agente_declarado"
    assert "dentro do mandato" in out["motivos"][-1]


def test_declared_agent_without_a_mandate_asks_the_owner(harness: Harness, keys) -> None:
    out = post(harness, signed(keys))
    assert (out["decisao"], out["acao"]) == ("desafiar", "confirmar_fora_da_sessao")
    assert out["ator"]["tipo"] == "agente_declarado"
    assert "sem mandato" in out["motivos"][-1]


@pytest.mark.parametrize(
    ("over", "why"),
    [
        ({"valor": 120.0}, "limite por compra"),
        ({"conta_id": "c_2"}, "outra conta"),
        ({"evento": "cadastro"}, "'cadastro'"),
    ],
)
def test_outside_the_mandate_asks_the_owner(harness: Harness, keys, over, why) -> None:
    out = post(harness, signed(keys, mandato=mandate(keys)) | over)
    assert (out["decisao"], out["acao"]) == ("desafiar", "confirmar_fora_da_sessao")
    assert why in out["motivos"][-1] or "cadastro" in out["motivos"][-1]


def test_total_budget_is_enforced_across_purchases(harness: Harness, keys) -> None:
    m = mandate(keys)
    assert post(harness, signed(keys, mandato=m, valor=90.0))["decisao"] == "aprovar"
    second = post(harness, signed(keys, mandato=m, valor=90.0))  # 180 > 150
    assert second["decisao"] == "desafiar" and "orçamento total" in second["motivos"][-1]


def test_mandate_for_another_agent_or_forged_is_not_accepted(harness: Harness, keys) -> None:
    out = post(harness, signed(keys, key="other", mandato=mandate(keys)))
    assert out["decisao"] == "desafiar" and "outro agente" in out["motivos"][-1]
    forged = mandate(keys)[:-4] + "AAAA"
    assert "mandato inválido" in post(harness, signed(keys, mandato=forged))["motivos"][-1]


def test_tampered_replayed_or_unknown_signatures_are_not_a_declaration(
    harness: Harness, keys
) -> None:
    tampered = signed(keys, mandato=mandate(keys))
    tampered["assinatura_agente"]["caminho"] = "/cadastro"  # signed for /checkout
    out = post(harness, tampered)
    assert out["ator"]["tipo"] != "agente_declarado"  # judged like anyone else
    assert "assinatura de agente apresentada, mas inválida" in out["motivos"]

    ev = signed(keys, mandato=mandate(keys))
    assert post(harness, ev)["decisao"] == "aprovar"
    replay = post(harness, ev | {"id_requisicao": "replay-1"})
    assert replay["ator"]["tipo"] != "agente_declarado"

    stranger = Ed25519PrivateKey.generate()
    keys["stranger"] = stranger
    out = post(harness, signed(keys, key="stranger", mandato=mandate(keys)))
    assert out["ator"]["tipo"] != "agente_declarado"


def test_expired_signature_is_rejected(harness: Harness, keys) -> None:
    from gandalf_engine.identity import OperatorDirectory, SignatureRejected, verify_signature

    old = time.time() - 3600
    h = sign_request(keys["agent"], OPERATOR, method="GET", authority=AUTHORITY, path="/", now=old)
    d = OperatorDirectory((OPERATOR,))
    d.keys[OPERATOR] = {
        thumbprint(public_jwk(keys["agent"].public_key())): keys["agent"].public_key()
    }
    with pytest.raises(SignatureRejected, match="expired"):
        verify_signature(
            signature=h["Signature"], signature_input=h["Signature-Input"],
            signature_agent=h["Signature-Agent"], method="GET", authority=AUTHORITY, path="/",
            directory=d,
        )  # fmt: skip


def test_outside_mandate_reasons_are_in_portuguese() -> None:
    from gandalf_engine.pipeline import outside_pt

    assert outside_pt("'checkout' not in the mandate") == "ação 'checkout' não autorizada no mandato"
    assert outside_pt("mandate rejected: mandate expired") == "mandato inválido"
    assert outside_pt("value above the per-purchase limit") == "valor acima do limite por compra"
