"""Level 5: a LEGITIMATE agent. It must get through. Lab use only.

    uv run python -m attackers.legit_agent --gerar-chave        # once
    uv run python -m attackers.legit_agent --sessions 40 --run nivel5

Each session plays both sides of a real delegation:

1. The account owner (a person) creates an account and, on the shop's "Agentes
   autorizados" page, grants a mandate to this agent's key: what it may do and how
   much it may spend. The shop signs the mandate.
2. The agent then shops for the owner: it signs every HTTP request with Web Bot Auth
   (its operator publishes the public key) and presents the mandate.

It does not pretend to be a person: plain browser automation, no telemetry tricks.
Some sessions deliberately step outside the mandate (an item above the per-purchase
limit), which must lead to the owner being asked, not to a silent approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gandalf_contracts.keys import b64u, private_from_b64u, public_jwk, thumbprint
from gandalf_engine.identity import sign_request

from simulator.common import (
    SHOP_URL,
    Actor,
    Clock,
    Truth,
    guard,
    identity,
    ip_datacenter,
    test_card,
)

KEY_DIR = Path("data/agente")
OPERATOR = "http://agent-operator:8090"  # must match the engine's allowlist
CHEAP = ("p6", "p3", "p12", "p11", "p1")  # within a R$ 100 limit
EXPENSIVE = "p5"  # R$ 899: above it


def generate_key() -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    (KEY_DIR / "chave.b64u").write_text(b64u(raw))
    (KEY_DIR / "chave.b64u").chmod(0o600)
    (KEY_DIR / "jwks.json").write_text(json.dumps({"keys": [public_jwk(key.public_key())]}))
    print("keyid:", thumbprint(public_jwk(key.public_key())))


def load_key() -> Ed25519PrivateKey:
    path = KEY_DIR / "chave.b64u"
    if not path.exists():
        raise SystemExit("no agent key: run  python -m attackers.legit_agent --gerar-chave")
    return private_from_b64u(path.read_text().strip())


async def owner_grants(
    base: str, rng: random.Random, keyid: str, clock: Clock, *, within: bool
) -> tuple[Any, str]:
    """The owner signs up and authorises the agent. Their own steps are not labelled:
    this script plays them only to set the scene."""
    ident = identity(rng)
    headers = {"X-Lab-Client-IP": f"187.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}",
               "X-Lab-Time-Offset": clock.header()}  # fmt: skip
    async with httpx.AsyncClient(base_url=base, verify=False, headers=headers, timeout=20) as c:  # noqa: S501
        await c.post(
            "/cadastro", data={"nome": ident.nome, "email": ident.email, "senha": ident.senha}
        )
        scope = ["login", "adicionar_carrinho", "checkout"]
        if not within and rng.random() < 0.5:
            scope = ["login", "adicionar_carrinho"]  # paying was never authorised
        r = await c.post(
            "/conta/agentes",
            data={
                "agente": keyid,
                "senha": ident.senha,
                "limite_valor": "100",
                "limite_acumulado": "250",
                "horas": "6",
                "escopo": scope,
            },  # fmt: skip
        )
        m = re.search(
            r"<code[^>]*>([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)</code>", r.text
        )
        if not m:
            raise RuntimeError("the shop did not issue a mandate")
        return ident, m.group(1)


async def agent_shops(
    browser: Any, base: str, rng: random.Random, key: Ed25519PrivateKey, ident: Any, token: str,
    clock: Clock, truth: Truth, who: Actor, sessao: str, *, within: bool,
) -> None:  # fmt: skip
    ctx = await browser.new_context(
        base_url=base,
        ignore_https_errors=True,
        extra_http_headers={
            "X-Lab-Client-IP": ip_datacenter(rng),
            "X-Lab-Time-Offset": clock.header(),
            "X-Mandato": token,
        },  # fmt: skip
    )

    async def sign(route: Any) -> None:
        req = route.request
        u = urlparse(req.url)
        headers = dict(req.headers) | sign_request(
            key, OPERATOR, method=req.method, authority=u.netloc, path=u.path or "/"
        )
        await route.continue_(headers=headers)

    await ctx.route("**/*", sign)
    page = await ctx.new_page()

    def on_response(resp: Any) -> None:
        if av := resp.headers.get("x-lab-avaliacao-id"):
            truth.record(av, resp.headers.get("x-lab-decisao", ""), who, sessao)

    page.on("response", on_response)
    try:
        await page.goto("/login")
        await page.fill('form[action="/login"] [name=email]', ident.email)
        await page.fill('form[action="/login"] [name=senha]', ident.senha)
        await page.click('form[action="/login"] button[type=submit]')
        await page.wait_for_load_state("domcontentloaded")
        pid = rng.choice(CHEAP) if within else EXPENSIVE
        await page.goto(f"/produto/{pid}")
        await page.click(".caixa-compra button")
        await page.wait_for_load_state("domcontentloaded")
        await page.goto("/checkout")
        if await page.locator('form[action="/checkout"]').count():
            await page.fill('form[action="/checkout"] [name=cartao]', test_card(rng))
            await page.click('form[action="/checkout"] button[type=submit]')
            await page.wait_for_load_state("domcontentloaded")
    finally:
        await ctx.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gerar-chave", action="store_true")
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument(
        "--fora", type=float, default=0.25, help="share of sessions outside the mandate"
    )
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--days", type=float, default=0.5)
    ap.add_argument("--run", default=time.strftime("nivel5-%Y%m%d-%H%M%S"))
    a = ap.parse_args()
    if a.gerar_chave:
        generate_key()
        return

    base = guard(SHOP_URL)
    key = load_key()
    keyid = thumbprint(public_jwk(key.public_key()))
    rng = random.Random(a.seed)
    truth, clock = Truth(a.run), Clock(a.days)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for n in range(1, a.sessions + 1):
            within = rng.random() >= a.fora
            perfil = "agente_legitimo" if within else "agente_fora_do_mandato"
            who = Actor("agente_declarado", 5, perfil, "compra_delegada", "navegador", True, False)
            try:
                ident, token = await owner_grants(base, rng, keyid, clock, within=within)
                await agent_shops(browser, base, rng, key, ident, token, clock, truth, who,
                                  f"{a.run}-{n}", within=within)  # fmt: skip
                status = "ok"
            except Exception as exc:
                status = f"falhou: {type(exc).__name__}: {exc}"[:120]
            clock.jump(a.days * 86_400 / a.sessions)
            print(f"{n}/{a.sessions} {perfil}: {status} ({truth.count} avaliações)", flush=True)
        await browser.close()
    print(f"gabarito: {truth.path}")


if __name__ == "__main__":
    asyncio.run(main())
