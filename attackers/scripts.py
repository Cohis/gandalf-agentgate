"""Attacker ladder, levels 1 and 2. Lab use only: every entry point goes through
``simulator.common.guard``, which refuses any target that is not the local lab.

Level 1  naive automation
    http      raw HTTP client posting forms; the page script never runs
    navegador stock Playwright: headless, ``navigator.webdriver`` true, instant fills

Level 2  stealth automation
    A patched browser with a clean environment (no webdriver flag, normal user
    agent). Behaviour is still a script's: clicks land on the exact centre with no
    pointer travel, keys arrive on a metronome, and the session goes straight to
    its goal.

Scenarios (the fraud each session attempts)
    fazenda_contas       synthetic sign-ups abusing the first-purchase coupon
    credential_stuffing  leaked e-mails tried slowly, a few per session
    card_testing         many cards, tiny purchases
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from simulator.common import Actor, Clock, Truth, identity, ip_datacenter, ip_residential, test_card
from simulator.human import World

SCENARIOS = ("fazenda_contas", "credential_stuffing", "card_testing")
CHEAP_ITEM = "p6"  # R$ 4,90: what a card tester buys


def actor(nivel: int, perfil: str, cenario: str, driver: str) -> Actor:
    return Actor("automacao_nao_declarada", nivel, perfil, cenario, driver, True, True)


def _emails(rng: random.Random, world: World, n: int) -> list[str]:
    known = world.victims()
    out = []
    for _ in range(n):
        leaked = known and rng.random() < 0.6
        out.append(rng.choice(known) if leaked else identity(rng).email)
    return out


# ---------------------------------------------------------------- level 1, raw HTTP


async def l1_http(
    base: str,
    rng: random.Random,
    clock: Clock,
    truth: Truth,
    world: World,
    cenario: str,
    sessao: str,
) -> None:
    who = actor(1, "script_http", cenario, "http")
    headers = {"X-Lab-Client-IP": ip_datacenter(rng), "X-Lab-Time-Offset": clock.header()}
    async with httpx.AsyncClient(base_url=base, verify=False, headers=headers, timeout=10) as c:  # noqa: S501 - self-signed lab certificate

        async def post(path: str, data: dict[str, str]) -> None:
            r = await c.post(path, data=data)
            if av := r.headers.get("x-lab-avaliacao-id"):
                truth.record(av, r.headers.get("x-lab-decisao", ""), who, sessao)

        if cenario == "credential_stuffing":
            for email in _emails(rng, world, rng.randint(2, 4)):
                await post("/login", {"email": email, "senha": identity(rng).senha})
                await asyncio.sleep(rng.uniform(0.1, 0.4))
            return
        ident = identity(rng)
        await post(
            "/cadastro",
            {"nome": ident.nome, "email": ident.email, "senha": ident.senha, "bio": ident.bio},
        )
        cards = 1 if cenario == "fazenda_contas" else rng.randint(2, 4)
        for _ in range(cards):
            await post("/carrinho/adicionar", {"pid": CHEAP_ITEM})
            data = {"cartao": test_card(rng, valid=cenario != "card_testing" or rng.random() < 0.7)}
            if cenario == "fazenda_contas":
                data["cupom"] = "PRIMEIRA10"
            await post("/checkout", data)
            await asyncio.sleep(rng.uniform(0.1, 0.4))


# ------------------------------------------------- browser scripts (levels 1 and 2)


class Script:
    """A scripted browser session. ``stealth`` only changes the pacing: the stealth
    *environment* comes from the browser build it is given (see simulator/run.py)."""

    def __init__(self, page: Any, rng: random.Random, world: World, *, stealth: bool) -> None:
        self.page, self.rng, self.world, self.stealth = page, rng, world, stealth
        self.key_delay = rng.choice((60, 80, 100, 120))  # one fixed cadence per session

    async def pause(self) -> None:
        # Level 1 waits a constant; level 2 knows to randomise, but uniformly.
        await asyncio.sleep(self.rng.uniform(0.7, 1.8) if self.stealth else 0.25)

    async def put(self, selector: str, text: str) -> None:
        loc = self.page.locator(selector)
        if self.stealth and self.rng.random() < 0.7:
            await loc.click()
            await loc.press_sequentially(text, delay=self.key_delay)
        else:
            await loc.fill(text)
        await self.pause()

    async def press(self, selector: str) -> None:
        async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
            await self.page.locator(selector).first.click()  # dead centre, no pointer travel
        await self.pause()

    async def signup(self) -> None:
        ident = identity(self.rng)
        await self.page.goto("/cadastro")
        f = 'form[action="/cadastro"]'
        await self.put(f"{f} [name=nome]", ident.nome)
        await self.put(f"{f} [name=email]", ident.email)
        await self.put(f"{f} [name=senha]", ident.senha)
        if ident.bio:
            await self.put(f"{f} [name=bio]", ident.bio)
        await self.press(f"{f} button[type=submit]")

    async def pay(self, *, coupon: bool, valid: bool) -> None:
        await self.page.goto("/")
        await self.press(f'form[action="/carrinho/adicionar"]:has([value="{CHEAP_ITEM}"]) button')
        await self.page.goto("/checkout")
        f = 'form[action="/checkout"]'
        if not await self.page.locator(f).count():
            return
        await self.put(f"{f} [name=cartao]", test_card(self.rng, valid=valid))
        if coupon:
            await self.put(f"{f} [name=cupom]", "PRIMEIRA10")
        await self.press(f"{f} button[type=submit]")

    async def run(self, cenario: str) -> None:
        if cenario == "credential_stuffing":
            f = 'form[action="/login"]'
            for email in _emails(self.rng, self.world, self.rng.randint(1, 3)):
                await self.page.goto("/login")
                await self.put(f"{f} [name=email]", email)
                await self.put(f"{f} [name=senha]", identity(self.rng).senha)
                await self.press(f"{f} button[type=submit]")
            return
        await self.signup()
        if cenario == "fazenda_contas":
            await self.pay(coupon=True, valid=True)
            return
        for _ in range(self.rng.randint(2, 4)):  # card testing
            await self.pay(coupon=False, valid=self.rng.random() < 0.7)


def attacker_ip(rng: random.Random, nivel: int) -> str:
    # Level 2 rents some residential exits; level 1 runs from a cloud VM.
    return ip_residential(rng) if nivel == 2 and rng.random() < 0.4 else ip_datacenter(rng)
