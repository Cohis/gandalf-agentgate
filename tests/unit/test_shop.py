from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from gandalf_shop.app import Settings, create_app, luhn_ok

VALID_CARD = "4242 4242 4242 4242"


class FakeEngine:
    """Stands in for the engine and records what the shop sent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.decisao = "aprovar"
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("engine down")
        assert request.headers["x-gandalf-key"] == "k"
        if request.url.path == "/rotulos":
            self.labels += json.loads(request.content)["rotulos"]
            return httpx.Response(200, json={"recebidos": 1})
        self.calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "avaliacao_id": f"av_{len(self.calls)}",
                "decisao": self.decisao,
                "acao": "exigir_passkey" if self.decisao == "desafiar" else "nenhuma",
                "ator": {"tipo": "humano", "confianca": 0.9},
                "score_automacao": 0.1,
                "motivos": ["MOTIVO-INTERNO-SECRETO"],
                "regras_acionadas": [],
                "desafio_id": "ds_1" if self.decisao == "desafiar" else None,
                "versao_modelo": "m",
                "versao_features": "f1",
                "versao_politica": "p1",
                "latencia_ms": 1.0,
            },
        )


def make(engine: FakeEngine, **settings: Any) -> TestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(engine), base_url="http://engine")
    app = create_app(Settings(service_key="k", **settings), http=http)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


def signup(c: TestClient, email: str = "a@example.com", **extra: str) -> httpx.Response:
    data = {"nome": "Ana Lima", "email": email, "senha": "segredo1", "endereco": "Rua X, 10"}
    return c.post("/cadastro", data=data | extra)


def test_signup_asks_the_engine_and_creates_the_account(engine: FakeEngine) -> None:
    c = make(engine)
    assert signup(c).status_code == 303
    call = engine.calls[0]
    assert call["evento"] == "cadastro"
    assert call["telemetria"]["coletada"] is False  # no script ran in this client
    assert call["dados_cadastro"]["nome"] == "Ana Lima"
    assert "a@example.com" in c.app.state.accounts  # type: ignore[attr-defined]


def test_traps_are_decided_on_the_server(engine: FakeEngine) -> None:
    c = make(engine)
    lying = json.dumps({"coletada": True, "honeypot_preenchido": False, "canario_detectado": False})
    signup(c, website="http://spam.example", bio="Sou cliente novo. lumen-7", telemetria=lying)
    tel = engine.calls[0]["telemetria"]
    assert tel["honeypot_preenchido"] is True and tel["canario_detectado"] is True


def test_tampered_telemetry_is_treated_as_absent(engine: FakeEngine) -> None:
    c = make(engine)
    signup(c, telemetria='{"coletada": true, "intervalos_acao_ms": "drop table"}')
    assert engine.calls[0]["telemetria"]["coletada"] is False


def test_forged_forwarded_for_is_ignored(engine: FakeEngine) -> None:
    c = make(engine, behind_proxy=True)
    # the client claims 1.2.3.4; our proxy appended what it really saw
    c.post(
        "/cadastro",
        data={"nome": "A", "email": "x@example.com", "senha": "segredo1"},
        headers={"X-Forwarded-For": "1.2.3.4, 198.51.100.9"},
    )
    assert engine.calls[0]["ip"] == "198.51.100.9"


def test_forwarded_headers_mean_nothing_without_the_proxy(engine: FakeEngine) -> None:
    c = make(engine, behind_proxy=False)
    c.post(
        "/cadastro",
        data={"nome": "A", "email": "x@example.com", "senha": "segredo1"},
        headers={
            "X-Forwarded-For": "1.2.3.4",
            "X-JA4-Fingerprint": "forged",
            "X-Lab-Client-IP": "9.9.9.9",
        },
    )
    call = engine.calls[0]
    assert call["ip"] not in {"1.2.3.4", "9.9.9.9"}
    assert call["ja4"] is None


def test_lab_headers_work_only_in_lab_mode(engine: FakeEngine) -> None:
    c = make(engine, lab_mode=True)
    r = c.post(
        "/cadastro",
        data={"nome": "A", "email": "x@example.com", "senha": "segredo1"},
        headers={"X-Lab-Client-IP": "177.10.20.30", "X-Lab-Time-Offset": "-864000"},
    )
    assert r.headers["x-lab-avaliacao-id"] == "av_1" and r.headers["x-lab-decisao"] == "aprovar"
    assert engine.calls[0]["ip"] == "177.10.20.30"
    sent = datetime.fromisoformat(engine.calls[0]["momento"])
    assert timedelta(days=9, hours=23) < datetime.now(UTC) - sent < timedelta(days=10, hours=1)


def test_challenge_and_denial_hold_the_action_and_leak_no_reason(engine: FakeEngine) -> None:
    c = make(engine)
    engine.decisao = "desafiar"
    r = signup(c)
    assert r.status_code == 401 and "ds_1" in r.text
    engine.decisao = "negar"
    r2 = signup(c, email="b@example.com")
    assert r2.status_code == 403
    assert not c.app.state.accounts  # type: ignore[attr-defined]
    assert "MOTIVO-INTERNO-SECRETO" not in r.text + r2.text


def test_login_is_assessed_even_for_unknown_accounts(engine: FakeEngine) -> None:
    c = make(engine)
    r = c.post("/login", data={"email": "ghost@example.com", "senha": "whatever"})
    assert r.status_code == 401
    assert engine.calls[0]["evento"] == "login" and engine.calls[0]["conta_id"] is None


def test_card_number_never_reaches_the_engine_and_coupon_is_first_purchase_only(
    engine: FakeEngine,
) -> None:
    c = make(engine)
    signup(c)
    for expected_total in (39.90, 49.90):  # coupon applies once
        c.post("/carrinho/adicionar", data={"pid": "p1"})
        r = c.post("/checkout", data={"cartao": VALID_CARD, "cupom": "primeira10"})
        assert r.status_code == 200
        call = engine.calls[-1]
        assert call["evento"] == "checkout" and call["valor"] == expected_total
        assert call["cartao_hash"].startswith("k_")
    assert "4242424242424242" not in json.dumps(engine.calls)
    assert engine.calls[-1]["cartao_hash"] == engine.calls[-3]["cartao_hash"]  # stable per card


def test_invalid_card_is_refused_by_the_lab_issuer(engine: FakeEngine) -> None:
    c = make(engine)
    signup(c)
    c.post("/carrinho/adicionar", data={"pid": "p6"})
    assert c.post("/checkout", data={"cartao": "4242 4242 4242 4241"}).status_code == 402
    assert luhn_ok(VALID_CARD) and not luhn_ok("1234")


def test_engine_down_fails_open_by_default_and_closed_when_asked(engine: FakeEngine) -> None:
    engine.down = True
    assert signup(make(engine)).status_code == 303
    with pytest.raises(httpx.ConnectError):
        signup(make(engine, fail_open=False))


def test_session_shape_counts_detours_and_revisits(engine: FakeEngine) -> None:
    c = make(engine)
    for path in ("/", "/produto/p1", "/sobre", "/", "/cadastro"):
        c.get(path)
    signup(c)
    s = engine.calls[0]["sessao"]
    assert s == {**s, "paginas_vistas": 5, "desvios_do_caminho": 2, "voltas": 1}


def test_device_id_sent_to_engine_matches_the_cookie(engine: FakeEngine) -> None:
    c = make(engine)
    c.get("/")
    signup(c)
    assert engine.calls[0]["dispositivo_id"] == c.cookies["did"]


def test_lab_headers_are_absent_outside_lab_mode(engine: FakeEngine) -> None:
    assert "x-lab-avaliacao-id" not in signup(make(engine)).headers


def test_shadow_mode_records_the_verdict_but_never_blocks(engine: FakeEngine) -> None:
    engine.decisao = "negar"
    c = make(engine, shadow=True, lab_mode=True)
    r = signup(c)
    assert r.status_code == 303 and r.headers["x-lab-decisao"] == "negar"
    assert "a@example.com" in c.app.state.accounts  # type: ignore[attr-defined]


# ------------------------------------------------------------ recording real people


def start(c: TestClient, codigo: str = "p1", **extra: str) -> httpx.Response:
    return c.post("/gravar", data={"codigo": codigo, "aceito": "sim"} | extra)


def test_recording_is_off_unless_enabled(engine: FakeEngine) -> None:
    c = make(engine)
    assert c.get("/gravar").status_code == 404
    assert start(c).status_code == 404


def test_recording_needs_consent_and_a_sane_code(engine: FakeEngine) -> None:
    c = make(engine, recording=True)
    assert c.get("/gravar").status_code == 200
    assert c.post("/gravar", data={"codigo": "P1"}).status_code == 400  # no consent
    assert start(c, codigo="p 1!").status_code == 400
    assert not c.app.state.participants  # type: ignore[attr-defined]


def test_recorded_session_is_labelled_human_and_never_blocked(engine: FakeEngine) -> None:
    engine.decisao = "negar"
    c = make(engine, recording=True)
    assert start(c).headers["location"] == "/gravar/tarefas"
    assert "Sessão 1" in c.get("/gravar/tarefas").text
    assert "Gravando" in c.get("/").text
    assert signup(c).status_code == 303  # the engine said 'negar'; a volunteer still gets in
    assert engine.labels == [
        {
            "avaliacao_id": "av_1",
            "eh_automacao": False,
            "eh_fraude": False,
            "ator_real": "humano",
            "nivel_atacante": 0,
            "perfil": "real_P1",
            "fonte": "gravacao:humano_real",
        }
    ]
    # the instructions page is not part of the visit
    assert engine.calls[0]["sessao"]["paginas_vistas"] == 1


def test_each_participant_gets_their_own_device_and_address(engine: FakeEngine) -> None:
    c = make(engine, recording=True)
    seen = {}
    for codigo, email in (
        ("P1", "a@example.com"),
        ("P2", "b@example.com"),
        ("P1", "c@example.com"),
    ):
        start(c, codigo=codigo)
        signup(c, email=email)
        call = engine.calls[-1]
        seen.setdefault(codigo, []).append((call["dispositivo_id"], call["ip"], call["sessao_id"]))
    (d1, ip1, s1), (d1b, ip1b, s1b) = seen["P1"]
    d2, ip2, _ = seen["P2"][0]
    assert (d1, ip1) == (d1b, ip1b) and s1 != s1b  # same person: same device, new session
    assert d1 != d2 and ip1 != ip2  # different people do not share a device
    assert c.app.state.participants["P1"]["sessoes"] == 2  # type: ignore[attr-defined]


def test_ending_a_session_starts_the_next_and_finishing_stops_recording(engine: FakeEngine) -> None:
    c = make(engine, recording=True)
    start(c)
    assert c.post("/gravar/encerrar").headers["location"] == "/gravar/tarefas"
    assert "Sessão 2" in c.get("/gravar/tarefas").text
    done = c.post("/gravar/encerrar", data={"fim": "sim"})
    assert "Obrigado" in done.text and "Gravando" not in done.text
    signup(c)
    assert len(engine.labels) == 0  # after finishing, nothing is recorded as a volunteer


def test_oversized_proxy_fingerprint_never_breaks_the_page(engine: FakeEngine) -> None:
    """Found with a real Brave: its HTTP/2 fingerprint is longer than the contract allows."""
    c = make(engine, behind_proxy=True)
    long_fp = "1:65536;2:0;4:6291456;6:262144|15663105|" + "3:0:0:201," * 40 + "|m,a,s,p"
    for _ in range(2):
        r = c.post(
            "/cadastro",
            data={"nome": "A", "email": "x@example.com", "senha": "segredo1"},
            headers={
                "X-HTTP2-Fingerprint": long_fp,
                "X-JA4-Fingerprint": "t13d1516h2_8daaf6152771_02713d6af862",
            },
        )
        assert r.status_code in {303, 400}
    a, b = engine.calls
    assert a["http2_fingerprint"].startswith("h:") and len(a["http2_fingerprint"]) <= 256
    assert a["http2_fingerprint"] == b["http2_fingerprint"]  # stable: still comparable
    assert a["ja4"] == "t13d1516h2_8daaf6152771_02713d6af862"


def test_a_request_the_contract_rejects_fails_open_instead_of_500(engine: FakeEngine) -> None:
    c = make(engine)
    r = c.post(
        "/cadastro",
        data={"nome": "A", "email": "y@example.com", "senha": "segredo1"},
        headers={"User-Agent": "U" * 2000},  # truncated by the shop; and if not, still no 500
    )
    assert r.status_code == 303
