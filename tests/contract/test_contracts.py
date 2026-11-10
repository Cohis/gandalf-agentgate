from __future__ import annotations

from typing import Any

import pytest
from gandalf_contracts import AssessRequest
from pydantic import ValidationError

from tests.conftest import KEY, Harness, make_event


def test_valid_example_parses() -> None:
    req = AssessRequest.model_validate(make_event())
    assert req.evento == "login"
    assert req.telemetria.coletada is True


def test_minimal_event_gets_safe_defaults() -> None:
    req = AssessRequest.model_validate(
        {
            "id_requisicao": "r",
            "evento": "cadastro",
            "momento": "2026-11-10T14:32:07Z",
            "dispositivo_id": "d",
            "sessao_id": "s",
            "ip": "2001:db8::1",
        }
    )
    assert req.telemetria.coletada is False  # no telemetry is itself a signal


def _tel(**over: Any) -> dict[str, Any]:
    return {"telemetria": {**make_event()["telemetria"], **over}}


MALFORMED: list[tuple[str, dict[str, Any]]] = [
    ("unknown top-level field", {"admin": True}),
    ("unknown event", {"evento": "reembolso"}),
    ("naive datetime", {"momento": "2026-11-10T14:32:07"}),
    ("garbage datetime", {"momento": "ontem"}),
    ("invalid ip", {"ip": "999.1.1.1"}),
    ("ip with port", {"ip": "10.0.0.1:443"}),
    ("empty device id", {"dispositivo_id": ""}),
    ("id with injection chars", {"conta_id": "c_1'; DROP TABLE events;--"}),
    ("id too long", {"sessao_id": "s" * 65}),
    ("negative value", {"valor": -1}),
    ("absurd value", {"valor": 1e12}),
    ("user agent too long", {"user_agent": "A" * 1025}),
    ("negative interval", _tel(intervalos_acao_ms=[-5])),
    ("too many intervals", _tel(intervalos_acao_ms=[100] * 201)),
    ("linearity above 1", _tel(trajetoria_cursor_linear=1.7)),
    ("unknown typing pattern", _tel(digitacao="telepatia")),
    ("unknown telemetry field", _tel(trajetoria_bruta=[[1, 2], [3, 4]])),
    ("webdriver not boolean", _tel(webdriver="talvez")),
    ("mandate too long", {"mandato": "x" * 8193}),
    ("signature missing parts", {"assinatura_agente": {"signature": "abc"}}),
    ("signup text too long", {"dados_cadastro": {"bio": "b" * 2001}}),
]


@pytest.mark.parametrize(("why", "patch"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_input_is_rejected(why: str, patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AssessRequest.model_validate(make_event(**patch))


@pytest.mark.parametrize(("why", "patch"), MALFORMED[:6], ids=[m[0] for m in MALFORMED[:6]])
def test_api_answers_422_never_500(harness: Harness, why: str, patch: dict[str, Any]) -> None:
    r = harness.client.post("/avaliar", json=make_event(**patch), headers={"X-Gandalf-Key": KEY})
    assert r.status_code == 422


def test_body_that_is_not_json_is_422(harness: Harness) -> None:
    r = harness.client.post(
        "/avaliar",
        content=b"{not json",
        headers={"X-Gandalf-Key": KEY, "Content-Type": "application/json"},
    )
    assert r.status_code == 422
