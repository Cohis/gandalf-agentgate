"""Lab shop: sign-up, login, catalogue and checkout with a first-purchase coupon.

It is deliberately small and server-rendered. State lives in memory: this is a
target for the lab's traffic generators, not a real store.

Before every sensitive action the backend asks the engine (POST /avaliar). The
browser never talks to the engine.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from gandalf_contracts import AssessRequest, AssessResponse, Decisao, Evento, Telemetria
from pydantic import ValidationError

log = logging.getLogger("gandalf.shop")
_HERE = Path(__file__).parent

PRODUCTS: dict[str, dict[str, Any]] = {
    "p1": {"nome": "Caneca Mithril", "preco": 49.90, "estoque": 120},
    "p2": {"nome": "Cajado de Viagem", "preco": 189.00, "estoque": 40},
    "p3": {"nome": "Mapa da Montanha", "preco": 24.90, "estoque": 300},
    "p4": {"nome": "Lanterna de Élfos", "preco": 129.00, "estoque": 60},
    "p5": {"nome": "Tênis Edição Limitada", "preco": 899.00, "estoque": 5},  # scalping bait
    "p6": {"nome": "Chá da Tarde", "preco": 4.90, "estoque": 999},  # card-testing bait
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


@dataclass(slots=True)
class Account:
    id: str
    email: str
    nome: str
    salt: bytes
    pw_hash: bytes
    orders: int = 0


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
    client = http or httpx.AsyncClient(base_url=s.engine_url, timeout=s.engine_timeout_s)

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
        open_path = path.startswith(("/gravar", "/static")) or path == "/saude"
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
            {"conta": account, "itens_carrinho": sum(sess.cart.values()), "canario": CANARY_TOKEN}
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

    def gate(request: Request, verdict: AssessResponse | None) -> Response | None:
        """None means 'go ahead'. Reasons are never shown to the visitor: they would
        teach an attacker what to change."""
        if s.shadow or verdict is None or verdict.decisao is Decisao.aprovar:
            return None
        if session_of(request)[1].rec:
            return None  # a volunteer is never blocked: the verdict is only recorded
        if verdict.decisao is Decisao.desafiar:
            return render(request, "desafio.html", status=401, desafio_id=verdict.desafio_id)
        return render(request, "negado.html", status=403)

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        return render(request, "home.html", produtos=PRODUCTS)

    @app.get("/produto/{pid}", response_class=HTMLResponse)
    async def product(request: Request, pid: str) -> Response:
        if pid not in PRODUCTS:
            return render(request, "negado.html", status=404, titulo="Produto não encontrado")
        return render(request, "produto.html", pid=pid, p=PRODUCTS[pid])

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
            dados_cadastro={"nome": nome, "endereco": endereco, "bio": bio},
        )
        if blocked := gate(request, verdict):
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

    @app.get("/saude")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    return create_app()
