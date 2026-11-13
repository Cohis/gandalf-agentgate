"""Lab shop: sign-up, login, catalogue and checkout with a first-purchase coupon.

It is deliberately small and server-rendered. State lives in memory: this is a
target for the lab's traffic generators, not a real store.

Before every sensitive action the backend asks the engine (POST /avaliar). The
browser never talks to the engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Any

import httpx
import webauthn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from gandalf_contracts import Acao, AssessRequest, AssessResponse, Decisao, Evento, Telemetria
from gandalf_contracts.keys import issue_mandate, private_from_b64u, public_jwk, thumbprint
from pydantic import ValidationError
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

log = logging.getLogger("gandalf.shop")
_HERE = Path(__file__).parent


def _p(nome: str, preco: float, cat: str, estoque: int, nota: float, aval: int, desc: str,
       antes: float | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "nome": nome, "preco": preco, "categoria": cat, "estoque": estoque, "nota": nota,
        "avaliacoes": aval, "descricao": desc, "antes": antes,
    }  # fmt: skip


CATEGORIES: dict[str, str] = {
    "casa": "Casa e decoração",
    "cozinha": "Cozinha",
    "mercearia": "Mercearia",
    "moda": "Moda",
    "papelaria": "Papelaria",
}
# Ratings and review counts are invented: this is a lab shop with no real customers.
# Ids and prices of p1, p3, p5 and p6 are relied on by the attack scenarios.
PRODUCTS: dict[str, dict[str, Any]] = {
    "p1": _p(
        "Xícara para Cappuccino 250 ml",
        49.90,
        "cozinha",
        120,
        4.7,
        312,
        "Cerâmica esmaltada com pires. Vai ao micro-ondas e à lava-louças.",
        antes=64.90,
    ),
    "p2": _p(
        "Sapato Oxford de Couro",
        189.00,
        "moda",
        40,
        4.4,
        87,
        "Couro legítimo, solado de borracha e palmilha acolchoada.",
        antes=239.00,
    ),
    "p3": _p(
        "Pôster Mapa Antigo de Londres",
        24.90,
        "casa",
        300,
        4.8,
        541,
        "Reprodução de um mapa de 1750 em papel fosco 30 x 40 cm.",
    ),
    "p4": _p(
        "Lanterna de Jardim em Pedra",
        129.00,
        "casa",
        60,
        4.5,
        64,
        "Esculpida em pedra, para vela ou LED. Resiste a chuva e sol.",
    ),
    # scalping bait: scarce and expensive
    "p5": _p(
        "Tênis Casual Edição Limitada",
        899.00,
        "moda",
        5,
        4.9,
        1203,
        "Lote numerado, cabedal em couro e cadarço encerado.",
    ),
    # card-testing bait: the cheapest thing in the shop
    "p6": _p(
        "Chá Verde Sachê Individual",
        4.90,
        "mercearia",
        999,
        4.3,
        958,
        "Um sachê de chá verde para uma xícara de 200 ml.",
    ),
    "p7": _p(
        "Mochila Estampada 20 L",
        149.00,
        "moda",
        75,
        4.6,
        229,
        "Tecido resistente à água, alças acolchoadas e bolso para notebook.",
        antes=179.00,
    ),
    "p8": _p(
        "Caderno Pautado com Caneta",
        39.90,
        "papelaria",
        210,
        4.6,
        176,
        "Capa dura, 160 páginas pautadas e caneta esferográfica.",
    ),
    "p9": _p(
        "Gravura Botânica Girassóis",
        59.90,
        "casa",
        90,
        4.2,
        41,
        "Impressão em papel algodão 21 x 30 cm, pronta para emoldurar.",
    ),
    "p10": _p(
        "Lanterna Decorativa Vermelha",
        79.90,
        "casa",
        150,
        4.4,
        133,
        "Tecido sobre armação de arame, com cordão para pendurar.",
        antes=99.90,
    ),
    "p11": _p(
        "Xícara de Porcelana com Pires",
        34.90,
        "cozinha",
        180,
        4.5,
        268,
        "Porcelana branca, 180 ml. Clássica para chá da tarde.",
    ),
    "p12": _p(
        "Mel Silvestre 300 g",
        32.90,
        "mercearia",
        140,
        4.8,
        407,
        "Mel puro de florada silvestre, em pote de vidro com tampa.",
    ),
}
FREE_SHIPPING_FROM = 200.0


def brl(value: float) -> str:
    """1234.5 -> '1.234,50' (Brazilian money format)."""
    return f"{value:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _fold(text: str) -> str:
    """Lower-case and strip accents, so 'xicara' finds 'Xícara'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def catalogue(q: str = "", categoria: str = "") -> dict[str, dict[str, Any]]:
    words = _fold(q).split()
    return {
        pid: p
        for pid, p in PRODUCTS.items()
        if (not categoria or p["categoria"] == categoria)
        and all(w in _fold(p["nome"] + " " + p["descricao"]) for w in words)
    }


COUPON = "PRIMEIRA10"
COUPON_OFF = 10.0
CANARY_TOKEN = "lumen-7"  # noqa: S105 - not a secret, it is the canary marker
# Pages on the shortest path to any goal. Anything else is a "detour" (humans wander).
DIRECT_PATH = {"/", "/cadastro", "/login", "/carrinho", "/checkout"}


@dataclass(frozen=True, slots=True)
class Settings:
    engine_url: str = "http://localhost:8000"
    service_key: str = "dev-only-change-me"
    card_hmac_key: str = "dev-only-change-me"
    behind_proxy: bool = False  # True only when reachable solely through the trusted proxy
    lab_mode: bool = False  # accepts simulated client IP / clock from the traffic generators
    fail_open: bool = True  # engine unreachable: let the customer through and log it
    # Shadow mode: ask the engine and record its verdict, but never block. This is how
    # training data is collected: blocking would censor everything an attacker does next.
    shadow: bool = False
    mandate_key: str = ""  # base64url Ed25519 private key; empty = a fresh one per start
    # Recording mode: real people, with consent, produce labelled human sessions.
    recording: bool = False
    # Public recording through a tunnel: the whole shop sits behind an invite code,
    # and the TLS fingerprints are dropped (they would be the tunnel's, not the visitor's).
    invite_code: str = ""
    tunnel: bool = False
    engine_timeout_s: float = 0.5

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        d = cls()
        return cls(
            engine_url=e.get("SHOP_ENGINE_URL", d.engine_url),
            service_key=e.get("GANDALF_SERVICE_KEY", d.service_key),
            card_hmac_key=e.get("SHOP_CARD_HMAC_KEY", d.card_hmac_key),
            behind_proxy=e.get("SHOP_BEHIND_PROXY", "0") == "1",
            lab_mode=e.get("SHOP_LAB_MODE", "0") == "1",
            fail_open=e.get("SHOP_FAIL_OPEN", "1") == "1",
            shadow=e.get("SHOP_SHADOW", "0") == "1",
            mandate_key=e.get("SHOP_MANDATE_KEY", ""),
            recording=e.get("SHOP_RECORDING", "0") == "1",
            invite_code=e.get("SHOP_INVITE_CODE", "").strip(),
            tunnel=e.get("SHOP_TUNNEL", "0") == "1",
        )


@dataclass(slots=True)
class Session:
    started: float
    pages: list[str] = field(default_factory=list)
    conta_id: str | None = None
    cart: dict[str, int] = field(default_factory=dict)
    rec: str | None = None  # participant code when this is a recorded human session
    rec_ip: str | None = None
    rec_n: int = 0
    verificada_ate: float = 0.0  # the owner proved it is them (passkey or e-mail link)
    webauthn_challenge: bytes | None = None


@dataclass(slots=True)
class Account:
    id: str
    email: str
    nome: str
    salt: bytes
    pw_hash: bytes
    orders: int = 0
    mandates: list[dict[str, Any]] = field(default_factory=list)
    passkeys: list[dict[str, Any]] = field(default_factory=list)  # id, public_key, sign_count


def agent_signature(request: Request) -> dict[str, str] | None:
    """Web Bot Auth headers, passed to the engine with what the signature covers. The
    shop does not verify them itself: the engine decides who is acting."""
    h = request.headers
    if not all(h.get(k) for k in ("signature", "signature-input", "signature-agent")):
        return None
    return {
        "signature": h["signature"][:2048],
        "signature_input": h["signature-input"][:2048],
        "signature_agent": h["signature-agent"][:1024],
        "metodo": request.method,
        "autoridade": h.get("host", "")[:255] or "desconhecido",
        "caminho": request.url.path[:2048],
    }


def _hash_pw(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 12 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def client_ip(request: Request, s: Settings) -> str:
    """The only IP we believe is the one our own proxy observed.

    A client can send any ``X-Forwarded-For`` it likes; the proxy *appends* the
    address it actually saw, so only the last entry is trustworthy, and only when
    the shop is not reachable except through that proxy.
    """
    if s.lab_mode and (lab := request.headers.get("x-lab-client-ip")):
        try:
            return str(ip_address(lab.strip()))
        except ValueError:
            pass
    if s.behind_proxy and (xff := request.headers.get("x-forwarded-for")):
        try:
            return str(ip_address(xff.split(",")[-1].strip()))
        except ValueError:
            pass
    try:
        return str(ip_address(request.client.host)) if request.client else "0.0.0.0"  # noqa: S104
    except ValueError:
        return "0.0.0.0"  # noqa: S104 - unix socket or test transport


def fingerprint(value: str | None, limit: int) -> str | None:
    """Fingerprints from the proxy have no fixed size (a real browser's HTTP/2 one can
    run past 300 characters). Long ones are replaced by a stable digest: equality is
    all the engine needs, and an oversized header must never break a customer's page."""
    if not value:
        return None
    if len(value) <= limit:
        return value
    return "h:" + hashlib.sha256(value.encode()).hexdigest()[:40]


def event_time(request: Request, s: Settings) -> datetime:
    """Lab only: generators may shift the clock to spread sessions over simulated days.
    The offset is added to the real clock, so timing inside a session stays real."""
    now = datetime.now(UTC)
    if s.lab_mode and (raw := request.headers.get("x-lab-time-offset")):
        try:
            return now + timedelta(seconds=max(-3.2e7, min(3.2e7, float(raw))))
        except ValueError:
            pass
    return now


def create_app(
    settings: Settings | None = None, *, http: httpx.AsyncClient | None = None
) -> FastAPI:
    s = settings or Settings.from_env()
    app = FastAPI(title="Loja de laboratório", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=_HERE / "templates")
    tpl.env.filters["brl"] = brl
    client = http or httpx.AsyncClient(base_url=s.engine_url, timeout=s.engine_timeout_s)

    # The key this shop signs mandates with. Lab default: a new key each start, so
    # mandates do not survive a restart (documented). Its public half is published.
    mandate_key = (
        private_from_b64u(s.mandate_key) if s.mandate_key else Ed25519PrivateKey.generate()
    )
    mandate_kid = "loja-" + thumbprint(public_jwk(mandate_key.public_key()))[:12]

    sessions: dict[str, Session] = {}
    accounts: dict[str, Account] = {}  # by e-mail
    app.state.sessions, app.state.accounts = sessions, accounts

    def session_of(request: Request) -> tuple[str, Session]:
        sid = getattr(request.state, "sid", None) or request.cookies.get("sid", "")
        if sid not in sessions:
            sid = "s_" + secrets.token_hex(12)
            sessions[sid] = Session(started=time.monotonic())
        request.state.sid = sid
        return sid, sessions[sid]

    @app.middleware("http")
    async def cookies(request: Request, call_next: Any) -> Response:
        """Every response carries the session and device cookies, redirects included."""
        path = request.url.path
        # The mandate key is public and is what the engine fetches: never behind the invite.
        open_path = path.startswith(("/gravar", "/static", "/.well-known/")) or path == "/saude"
        if s.invite_code and not open_path:
            # Invitation only: without a recorded session there is nothing to see.
            sess = sessions.get(request.cookies.get("sid", ""))
            if not (sess and sess.rec):
                return RedirectResponse("/gravar", status_code=303)
        response: Response = await call_next(request)
        if path.startswith("/static") or path == "/saude":
            return response
        verdict = getattr(request.state, "lab_verdict", None)
        if s.lab_mode and verdict is not None:
            # Lets the lab's traffic generators join ground truth to the decision.
            response.headers["x-lab-avaliacao-id"] = verdict.avaliacao_id
            response.headers["x-lab-decisao"] = verdict.decisao.value
        opts: dict[str, Any] = {"httponly": True, "samesite": "lax", "secure": s.behind_proxy}
        response.set_cookie("sid", session_of(request)[0], **opts)
        response.set_cookie("did", device_of(request), max_age=31_536_000, **opts)
        return response

    def device_of(request: Request) -> str:
        """First-party device cookie. Cached per request so the id sent to the engine
        is the same one written back to the browser."""
        if cached := getattr(request.state, "did", None):
            return str(cached)
        did = request.cookies.get("did", "")
        ok = did.startswith("d_") and len(did) == 26 and did[2:].isalnum()
        request.state.did = did if ok else "d_" + secrets.token_hex(12)
        return str(request.state.did)

    def render(
        request: Request, name: str, status: int = 200, *, track: bool = True, **ctx: Any
    ) -> Response:
        sess = session_of(request)[1]
        if track and request.method == "GET":
            sess.pages.append(request.url.path)
        if sess.rec:
            ctx.setdefault("gravacao", {"codigo": sess.rec, "n": sess.rec_n})
        account = next((a for a in accounts.values() if a.id == sess.conta_id), None)
        resp = tpl.TemplateResponse(
            request,
            name,
            {
                "conta": account,
                "itens_carrinho": sum(sess.cart.values()),
                "canario": CANARY_TOKEN,
                "categorias": CATEGORIES,
                "frete_gratis_de": FREE_SHIPPING_FROM,
                "q": "",
                "categoria": "",
            }
            | ctx,
            status_code=status,
        )
        return resp

    def telemetry_from(raw: str, *, honeypot: str, free_text: str) -> Telemetria:
        try:
            tel = Telemetria.model_validate(json.loads(raw)) if raw else Telemetria(coletada=False)
        except (ValueError, ValidationError):
            tel = Telemetria(coletada=False)  # tampered or broken payload: treat as absent
        # Traps are checked on the server: a client cannot be trusted to report them.
        return tel.model_copy(
            update={
                "honeypot_preenchido": bool(honeypot.strip()),
                "canario_detectado": CANARY_TOKEN in free_text.lower(),
            }
        )

    async def assess(
        request: Request, evento: Evento, tel: Telemetria, **extra: Any
    ) -> AssessResponse | None:
        sid, sess = session_of(request)
        seen: set[str] = set()
        revisits = sum(1 for p in sess.pages if p in seen or seen.add(p))  # type: ignore[func-returns-value]
        try:
            # Building the request is inside the guard too: nothing about risk scoring
            # may ever turn into an error page for a customer.
            payload = AssessRequest.model_validate(
                {
                    "id_requisicao": "rq_" + secrets.token_hex(12),
                    "evento": evento,
                    "momento": event_time(request, s),
                    "conta_id": sess.conta_id,
                    "dispositivo_id": device_of(request),
                    "sessao_id": sid,
                    "ip": sess.rec_ip or client_ip(request, s),
                    "user_agent": request.headers.get("user-agent", "")[:1024],
                    # Fingerprints are only meaningful when our proxy computed them.
                    "ja4": (
                        fingerprint(request.headers.get("x-ja4-fingerprint"), 64)
                        if s.behind_proxy and not s.tunnel
                        else None
                    ),
                    "http2_fingerprint": (
                        fingerprint(request.headers.get("x-http2-fingerprint"), 256)
                        if s.behind_proxy and not s.tunnel
                        else None
                    ),
                    "telemetria": tel,
                    "assinatura_agente": agent_signature(request),
                    "mandato": (request.headers.get("x-mandato") or "")[:8192] or None,
                    "sessao": {
                        "paginas_vistas": len(sess.pages),
                        "segundos_desde_inicio": round(time.monotonic() - sess.started, 3),
                        "desvios_do_caminho": sum(1 for p in sess.pages if p not in DIRECT_PATH),
                        "voltas": revisits,
                    },
                }
                | extra
            )
            r = await client.post(
                "/avaliar",
                content=payload.model_dump_json(),
                headers={"X-Gandalf-Key": s.service_key, "Content-Type": "application/json"},
            )
            r.raise_for_status()
            verdict = AssessResponse.model_validate_json(r.content)
            request.state.lab_verdict = verdict
            if sess.rec:
                await label_recorded(verdict.avaliacao_id, sess.rec)
            return verdict
        except (httpx.HTTPError, ValidationError):
            log.exception("engine unavailable for %s", evento)
            if s.fail_open:
                return None
            raise

    async def label_recorded(avaliacao_id: str, codigo: str) -> None:
        """A recorded session is known to be a person: tell the engine right away."""
        label = {
            "avaliacao_id": avaliacao_id,
            "eh_automacao": False,
            "eh_fraude": False,
            "ator_real": "humano",
            "nivel_atacante": 0,
            "perfil": f"real_{codigo}",
            "fonte": "gravacao:humano_real",
        }
        try:
            r = await client.post(
                "/rotulos", json={"rotulos": [label]}, headers={"X-Gandalf-Key": s.service_key}
            )
            r.raise_for_status()
        except httpx.HTTPError:
            log.exception("could not deliver the label for %s", avaliacao_id)

    def gate(
        request: Request, verdict: AssessResponse | None, email: str | None = None
    ) -> Response | None:
        """None means 'go ahead'. Reasons are never shown to the visitor: they would
        teach an attacker what to change."""
        # Lab only: a single request may ask to be enforced even in shadow mode, so the
        # tests and the legitimate agent can meet the real challenges.
        enforce = s.lab_mode and request.headers.get("x-lab-enforce") == "1"
        if (s.shadow and not enforce) or verdict is None or verdict.decisao is Decisao.aprovar:
            return None
        sid, sess = session_of(request)
        if sess.rec:
            return None  # a volunteer is never blocked: the verdict is only recorded
        if verdict.decisao is not Decisao.desafiar:
            return render(request, "negado.html", status=403)
        if sess.verificada_ate > time.monotonic():
            return None  # the owner has just proved it is them in this session
        acct = logged_in(request)
        if verdict.acao is Acao.exigir_passkey and acct and acct.passkeys:
            return render(request, "desafio.html", status=401, modo="passkey",
                          desafio_id=verdict.desafio_id)  # fmt: skip
        target = (acct.email if acct else email) or ""
        if not target:
            return render(request, "desafio.html", status=401, modo="retido",
                          desafio_id=verdict.desafio_id)  # fmt: skip
        token = secrets.token_urlsafe(18)
        confirmations[token] = {"sid": sid, "email": target, "desafio_id": verdict.desafio_id,
                                "criado": time.time()}  # fmt: skip
        masked = target[0] + "***" + target[target.find("@") :] if "@" in target else "***"
        return render(request, "desafio.html", status=401, modo="email", destino=masked,
                      desafio_id=verdict.desafio_id, laboratorio=s.lab_mode)  # fmt: skip

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, q: str = "", categoria: str = "") -> Response:
        q = q.strip()[:60]
        categoria = categoria if categoria in CATEGORIES else ""
        ofertas = {pid: p for pid, p in PRODUCTS.items() if p["antes"]}
        return render(
            request,
            "home.html",
            produtos=catalogue(q, categoria),
            ofertas=ofertas,
            q=q,
            categoria=categoria,
        )

    @app.get("/produto/{pid}", response_class=HTMLResponse)
    async def product(request: Request, pid: str) -> Response:
        if pid not in PRODUCTS:
            return render(request, "negado.html", status=404, titulo="Produto não encontrado")
        p = PRODUCTS[pid]
        same = [k for k, v in PRODUCTS.items() if v["categoria"] == p["categoria"] and k != pid]
        rest = [k for k in PRODUCTS if k != pid and k not in same]
        relacionados = {k: PRODUCTS[k] for k in (same + rest)[:4]}
        return render(request, "produto.html", pid=pid, p=p, relacionados=relacionados)

    @app.get("/sobre", response_class=HTMLResponse)
    async def about(request: Request) -> Response:
        return render(request, "sobre.html")

    @app.get("/cadastro", response_class=HTMLResponse)
    async def signup_form(request: Request) -> Response:
        return render(request, "cadastro.html")

    @app.post("/cadastro", response_class=HTMLResponse)
    async def signup(
        request: Request,
        nome: Annotated[str, Form(max_length=120)],
        email: Annotated[str, Form(max_length=200)],
        senha: Annotated[str, Form(min_length=6, max_length=200)],
        endereco: Annotated[str, Form(max_length=300)] = "",
        bio: Annotated[str, Form(max_length=1000)] = "",
        website: Annotated[str, Form(max_length=300)] = "",  # honeypot, hidden by CSS
        telemetria: Annotated[str, Form(max_length=8000)] = "",
    ) -> Response:
        email = email.strip().lower()
        tel = telemetry_from(telemetria, honeypot=website, free_text=bio)
        verdict = await assess(
            request,
            Evento.cadastro,
            tel,
            dados_cadastro={"nome": nome, "email": email, "endereco": endereco, "bio": bio},
        )
        if blocked := gate(request, verdict, email=email):
            return blocked
        if "@" not in email or email in accounts:
            return render(request, "cadastro.html", status=400, erro="E-mail inválido ou em uso.")
        salt = secrets.token_bytes(16)
        acct = Account("c_" + secrets.token_hex(8), email, nome, salt, _hash_pw(senha, salt))
        accounts[email] = acct
        session_of(request)[1].conta_id = acct.id
        return RedirectResponse("/", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        return render(request, "login.html")

    @app.post("/login", response_class=HTMLResponse)
    async def login(
        request: Request,
        email: Annotated[str, Form(max_length=200)],
        senha: Annotated[str, Form(max_length=200)],
        website: Annotated[str, Form(max_length=300)] = "",
        telemetria: Annotated[str, Form(max_length=8000)] = "",
    ) -> Response:
        acct = accounts.get(email.strip().lower())
        tel = telemetry_from(telemetria, honeypot=website, free_text="")
        # Assessed before the password check, with the *claimed* account: credential
        # stuffing must be visible even when every password is wrong.
        verdict = await assess(request, Evento.login, tel, conta_id=acct.id if acct else None)
        if blocked := gate(request, verdict):
            return blocked
        if not acct or not hmac.compare_digest(_hash_pw(senha, acct.salt), acct.pw_hash):
            return render(request, "login.html", status=401, erro="E-mail ou senha incorretos.")
        session_of(request)[1].conta_id = acct.id
        return RedirectResponse("/", status_code=303)

    @app.post("/sair")
    async def logout(request: Request) -> Response:
        session_of(request)[1].conta_id = None
        return RedirectResponse("/", status_code=303)

    @app.post("/carrinho/adicionar", response_class=HTMLResponse)
    async def add_to_cart(
        request: Request,
        pid: Annotated[str, Form(max_length=8)],
        telemetria: Annotated[str, Form(max_length=8000)] = "",
    ) -> Response:
        if pid not in PRODUCTS:
            return render(request, "negado.html", status=404, titulo="Produto não encontrado")
        tel = telemetry_from(telemetria, honeypot="", free_text="")
        verdict = await assess(
            request, Evento.adicionar_carrinho, tel, valor=PRODUCTS[pid]["preco"]
        )
        if blocked := gate(request, verdict):
            return blocked
        cart = session_of(request)[1].cart
        cart[pid] = cart.get(pid, 0) + 1
        return RedirectResponse("/carrinho", status_code=303)

    def cart_view(sess: Session) -> tuple[list[dict[str, Any]], float]:
        lines = [
            {"pid": pid, "qtd": q, **PRODUCTS[pid], "subtotal": PRODUCTS[pid]["preco"] * q}
            for pid, q in sess.cart.items()
        ]
        return lines, round(sum(ln["subtotal"] for ln in lines), 2)

    @app.post("/carrinho/remover")
    async def remove_from_cart(
        request: Request, pid: Annotated[str, Form(max_length=8)]
    ) -> Response:
        session_of(request)[1].cart.pop(pid, None)
        return RedirectResponse("/carrinho", status_code=303)

    @app.get("/carrinho", response_class=HTMLResponse)
    async def cart(request: Request) -> Response:
        lines, total = cart_view(session_of(request)[1])
        return render(request, "carrinho.html", linhas=lines, total=total)

    @app.get("/checkout", response_class=HTMLResponse)
    async def checkout_form(request: Request) -> Response:
        sess = session_of(request)[1]
        if not sess.conta_id:
            return RedirectResponse("/login", status_code=303)
        lines, total = cart_view(sess)
        return render(request, "checkout.html", linhas=lines, total=total, cupom=COUPON)

    @app.post("/checkout", response_class=HTMLResponse)
    async def checkout(
        request: Request,
        cartao: Annotated[str, Form(max_length=40)],
        cupom: Annotated[str, Form(max_length=40)] = "",
        observacoes: Annotated[str, Form(max_length=1000)] = "",
        website: Annotated[str, Form(max_length=300)] = "",
        telemetria: Annotated[str, Form(max_length=8000)] = "",
    ) -> Response:
        sess = session_of(request)[1]
        acct = next((a for a in accounts.values() if a.id == sess.conta_id), None)
        if not acct:
            return RedirectResponse("/login", status_code=303)
        lines, total = cart_view(sess)
        if not lines:
            return RedirectResponse("/carrinho", status_code=303)
        first_purchase = cupom.strip().upper() == COUPON and acct.orders == 0
        if first_purchase:
            total = max(round(total - COUPON_OFF, 2), 0.0)
        digits = "".join(c for c in cartao if c.isdigit())
        # The card number stops here: only a keyed hash goes to the engine, nothing is stored.
        card_hash = (
            "k_" + hmac.new(s.card_hmac_key.encode(), digits.encode(), "sha256").hexdigest()[:24]
        )
        tel = telemetry_from(telemetria, honeypot=website, free_text=observacoes)
        verdict = await assess(request, Evento.checkout, tel, cartao_hash=card_hash, valor=total)
        if blocked := gate(request, verdict):
            return blocked
        if not luhn_ok(digits):  # the lab's "issuer"
            return render(
                request, "checkout.html", status=402, linhas=lines, total=total, cupom=COUPON,
                erro="Cartão recusado pelo emissor.",
            )  # fmt: skip
        acct.orders += 1
        sess.cart.clear()
        return render(request, "pedido.html", total=total, cupom_aplicado=first_purchase)

    # ------------------------------------------------- recording real people

    participants: dict[str, dict[str, Any]] = {}
    app.state.participants = participants

    def start_recorded_session(request: Request, codigo: str) -> None:
        """A fresh session for the participant. Each participant gets a device and an
        address of their own, so five friends on one notebook do not look like five
        accounts on one device."""
        me = participants.setdefault(
            codigo,
            {
                "did": "d_" + secrets.token_hex(12),
                "ip": f"187.{secrets.randbelow(256)}.{secrets.randbelow(256)}."
                f"{secrets.randbelow(253) + 1}",
                "sessoes": 0,
            },
        )
        me["sessoes"] += 1
        sid = "s_" + secrets.token_hex(12)
        sessions[sid] = Session(
            started=time.monotonic(), rec=codigo, rec_ip=me["ip"], rec_n=me["sessoes"]
        )
        request.state.sid, request.state.did = sid, me["did"]

    consent_ctx: dict[str, Any] = {"pede_convite": bool(s.invite_code), "tunel": s.tunnel}

    def recording_off(request: Request) -> Response | None:
        if not s.recording:
            return render(request, "negado.html", status=404, track=False, titulo="Não encontrado")
        return None

    @app.get("/gravar", response_class=HTMLResponse)
    async def record_consent(request: Request, convite: str = "") -> Response:
        return recording_off(request) or render(
            request, "gravar.html", track=False, convite=convite[:32], **consent_ctx
        )

    @app.post("/gravar", response_class=HTMLResponse)
    async def record_start(
        request: Request,
        codigo: Annotated[str, Form(max_length=8)],
        aceito: Annotated[str, Form(max_length=3)] = "",
        convite: Annotated[str, Form(max_length=32)] = "",
    ) -> Response:
        if off := recording_off(request):
            return off
        if s.invite_code and not hmac.compare_digest(
            convite.strip().encode(), s.invite_code.encode()
        ):
            await asyncio.sleep(1.0)  # makes guessing the invitation slow
            return render(
                request, "gravar.html", status=403, track=False, **consent_ctx,
                erro="Convite inválido. Confira o link que você recebeu.",
            )  # fmt: skip
        codigo = codigo.strip().upper()
        if not (2 <= len(codigo) <= 8 and codigo.isalnum() and codigo.isascii()):
            return render(
                request, "gravar.html", status=400, track=False, convite=convite, **consent_ctx,
                erro="O código deve ter de 2 a 8 letras ou números, sem espaços.",
            )  # fmt: skip
        if aceito != "sim":
            return render(
                request, "gravar.html", status=400, track=False, convite=convite, **consent_ctx,
                erro="Sem o seu consentimento a sessão não é gravada.",
            )  # fmt: skip
        start_recorded_session(request, codigo)
        return RedirectResponse("/gravar/tarefas", status_code=303)

    @app.get("/gravar/tarefas", response_class=HTMLResponse)
    async def record_tasks(request: Request) -> Response:
        if off := recording_off(request):
            return off
        sess = session_of(request)[1]
        if not sess.rec:
            return RedirectResponse("/gravar", status_code=303)
        sess.started = time.monotonic()  # reading the instructions is not part of the visit
        return render(request, "tarefas.html", track=False)

    @app.post("/gravar/encerrar", response_class=HTMLResponse)
    async def record_next(
        request: Request, fim: Annotated[str, Form(max_length=3)] = ""
    ) -> Response:
        if off := recording_off(request):
            return off
        codigo = session_of(request)[1].rec
        if not codigo:
            return RedirectResponse("/gravar", status_code=303)
        if fim == "sim":
            total = participants[codigo]["sessoes"]
            # leave an ordinary, unrecorded session and a new device behind
            sid = "s_" + secrets.token_hex(12)
            sessions[sid] = Session(started=time.monotonic())
            request.state.sid, request.state.did = sid, "d_" + secrets.token_hex(12)
            return render(request, "gravar_fim.html", track=False, total=total, gravacao=None)
        start_recorded_session(request, codigo)
        return RedirectResponse("/gravar/tarefas", status_code=303)

    # ------------------------------------------------------- mandates for agents

    SCOPES = {"login": "Entrar na conta", "adicionar_carrinho": "Pôr no carrinho",
              "checkout": "Pagar"}  # fmt: skip

    def logged_in(request: Request) -> Account | None:
        sess = session_of(request)[1]
        return next((a for a in accounts.values() if a.id == sess.conta_id), None)

    @app.get("/.well-known/gandalf-mandato")
    async def mandate_public_key() -> dict[str, Any]:
        return {"kid": mandate_kid, "jwk": public_jwk(mandate_key.public_key())}

    @app.get("/conta/agentes", response_class=HTMLResponse)
    async def agents_page(request: Request) -> Response:
        acct = logged_in(request)
        if not acct:
            return RedirectResponse("/login", status_code=303)
        return render(request, "conta_agentes.html", escopos=SCOPES, mandatos=acct.mandates)

    @app.post("/conta/agentes", response_class=HTMLResponse)
    async def grant_mandate(
        request: Request,
        agente: Annotated[str, Form(min_length=20, max_length=64)],
        senha: Annotated[str, Form(max_length=200)],
        limite_valor: Annotated[float, Form(gt=0, le=10_000)],
        limite_acumulado: Annotated[float, Form(gt=0, le=50_000)],
        horas: Annotated[int, Form(ge=1, le=720)] = 24,
        escopo: Annotated[list[str] | None, Form()] = None,
    ) -> Response:
        acct = logged_in(request)
        if not acct:
            return RedirectResponse("/login", status_code=303)
        escolhidos = [e for e in (escopo or []) if e in SCOPES]

        def again(erro: str) -> Response:
            return render(request, "conta_agentes.html", status=400, escopos=SCOPES,
                          mandatos=acct.mandates, erro=erro)  # fmt: skip

        # The owner confirms it is them. Phase 5 adds the passkey; the password is the
        # fallback for accounts without one.
        if not hmac.compare_digest(_hash_pw(senha, acct.salt), acct.pw_hash):
            return again("Senha incorreta: o mandato não foi criado.")
        if not escolhidos:
            return again("Escolha pelo menos uma ação que o agente pode fazer.")
        if limite_acumulado < limite_valor:
            return again("O limite total não pode ser menor que o limite por compra.")
        jti = "m_" + secrets.token_hex(8)
        exp = int(time.time()) + horas * 3600
        claims = {
            "jti": jti, "sub": acct.id, "agente": agente.strip(), "escopo": escolhidos,
            "limite_valor": limite_valor, "limite_acumulado": limite_acumulado, "exp": exp,
        }  # fmt: skip
        token = issue_mandate(mandate_key, mandate_kid, claims)
        acct.mandates.append(claims | {"token": token, "revogado": False})
        return render(request, "conta_agentes.html", escopos=SCOPES, mandatos=acct.mandates,
                      novo=token)  # fmt: skip

    @app.post("/conta/agentes/{jti}/revogar")
    async def revoke_mandate(request: Request, jti: str) -> Response:
        acct = logged_in(request)
        if not acct:
            return RedirectResponse("/login", status_code=303)
        for m in acct.mandates:
            if m["jti"] == jti and not m["revogado"]:
                r = await client.post(
                    f"/mandatos/{jti}/revogar", headers={"X-Gandalf-Key": s.service_key}
                )
                m["revogado"] = r.status_code == 200
        return RedirectResponse("/conta/agentes", status_code=303)

    # ------------------------------------- passkeys and out-of-session confirmation

    confirmations: dict[str, dict[str, Any]] = {}

    def webauthn_site(request: Request) -> tuple[str, str]:
        host = request.headers.get("host", "localhost")
        scheme = "https" if s.behind_proxy else request.url.scheme
        return host.split(":")[0], f"{scheme}://{host}"

    async def report_challenge(desafio_id: str | None, estado: str) -> None:
        if not desafio_id:
            return
        with contextlib.suppress(httpx.HTTPError):
            await client.post(
                f"/desafios/{desafio_id}/resultado",
                json={"estado": estado},
                headers={"X-Gandalf-Key": s.service_key},
            )

    @app.get("/conta/passkey", response_class=HTMLResponse)
    async def passkey_page(request: Request) -> Response:
        acct = logged_in(request)
        if not acct:
            return RedirectResponse("/login", status_code=303)
        return render(request, "conta_passkey.html", total=len(acct.passkeys))

    @app.post("/conta/passkey/opcoes")
    async def passkey_options(request: Request) -> Response:
        acct = logged_in(request)
        if not acct:
            return JSONResponse({"erro": "entre na conta"}, status_code=401)
        rp_id, _ = webauthn_site(request)
        opts = webauthn.generate_registration_options(
            rp_id=rp_id,
            rp_name="Loja da Montanha",
            user_id=acct.id.encode(),
            user_name=acct.email,
            user_display_name=acct.nome,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=k["id"]) for k in acct.passkeys],
        )
        session_of(request)[1].webauthn_challenge = opts.challenge
        return Response(webauthn.options_to_json(opts), media_type="application/json")

    @app.post("/conta/passkey/registrar")
    async def passkey_register(request: Request) -> Response:
        acct, sess = logged_in(request), session_of(request)[1]
        if not acct or not sess.webauthn_challenge:
            return JSONResponse({"erro": "sem desafio pendente"}, status_code=400)
        rp_id, origin = webauthn_site(request)
        try:
            v = webauthn.verify_registration_response(
                credential=(await request.body()).decode()[:20_000],
                expected_challenge=sess.webauthn_challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )
        except Exception:
            return JSONResponse({"erro": "passkey não verificada"}, status_code=400)
        finally:
            sess.webauthn_challenge = None
        acct.passkeys.append({"id": v.credential_id, "public_key": v.credential_public_key,
                              "sign_count": v.sign_count})  # fmt: skip
        return JSONResponse({"ok": True, "total": len(acct.passkeys)})

    @app.post("/desafio/{desafio_id}/opcoes")
    async def challenge_options(request: Request, desafio_id: str) -> Response:
        acct = logged_in(request)
        if not acct or not acct.passkeys:
            return JSONResponse({"erro": "sem passkey"}, status_code=400)
        rp_id, _ = webauthn_site(request)
        opts = webauthn.generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=[PublicKeyCredentialDescriptor(id=k["id"]) for k in acct.passkeys],
        )
        session_of(request)[1].webauthn_challenge = opts.challenge
        return Response(webauthn.options_to_json(opts), media_type="application/json")

    @app.post("/desafio/{desafio_id}/confirmar")
    async def challenge_confirm(request: Request, desafio_id: str) -> Response:
        """The owner's authenticator answered: whoever holds only the password, or a
        script driving the browser, cannot produce this."""
        acct, sess = logged_in(request), session_of(request)[1]
        if not acct or not sess.webauthn_challenge:
            return JSONResponse({"erro": "sem desafio pendente"}, status_code=400)
        body = (await request.body()).decode()[:20_000]
        rp_id, origin = webauthn_site(request)
        try:
            cred_id = webauthn.base64url_to_bytes(json.loads(body)["rawId"])
            key = next(k for k in acct.passkeys if k["id"] == cred_id)
            v = webauthn.verify_authentication_response(
                credential=body,
                expected_challenge=sess.webauthn_challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=key["public_key"],
                credential_current_sign_count=key["sign_count"],
            )
        except Exception:
            await report_challenge(desafio_id, "falhou")
            return JSONResponse({"erro": "passkey não confere"}, status_code=400)
        finally:
            sess.webauthn_challenge = None
        key["sign_count"] = v.new_sign_count
        sess.verificada_ate = time.monotonic() + 600
        await report_challenge(desafio_id, "aprovado")
        return JSONResponse({"ok": True})

    @app.get("/caixa-postal", response_class=HTMLResponse)
    async def mailbox(request: Request, email: str = "") -> Response:
        """Lab only: stands in for the owner's e-mail, a channel outside this session."""
        if not s.lab_mode:
            return render(request, "negado.html", status=404, track=False, titulo="Não encontrado")
        email = email.strip().lower()[:200]
        msgs = [
            {"token": t, **c} for t, c in confirmations.items() if email and c["email"] == email
        ]
        return render(request, "caixa_postal.html", track=False, email=email, mensagens=msgs)

    @app.get("/confirmar/{token}", response_class=HTMLResponse)
    async def confirm_link(request: Request, token: str) -> Response:
        c = confirmations.pop(token, None)
        if not c or time.time() - c["criado"] > 1800:
            return render(request, "negado.html", status=404, track=False,
                          titulo="Link inválido ou expirado")  # fmt: skip
        if c["sid"] in sessions:
            sessions[c["sid"]].verificada_ate = time.monotonic() + 600
        await report_challenge(c["desafio_id"], "aprovado")
        return render(request, "confirmado.html", track=False)

    @app.get("/saude")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    return create_app()
