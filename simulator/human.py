"""Simulated people, driven through a real browser against the lab shop.

What is real: the browser, TLS/JA4, the page script, and the pointer paths (replayed
from human recordings). What is modelled: think time, typing cadence and which pages
a person wanders through. docs/limitations.md says so explicitly.

The "hard" personas exist to measure false positives: they legitimately look like
automation in one dimension each (pasted credentials, perfectly centred clicks, no
mouse at all, many people on one IP, a new device, a new country).
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .common import (
    Actor,
    Identity,
    identity,
    ip_abroad,
    ip_mobile_cgnat,
    ip_residential,
    test_card,
)
from .strokes import Stroke, path_between


@dataclass(frozen=True, slots=True)
class Persona:
    nome: str
    peso: float
    ip: Callable[[random.Random], str] = ip_residential
    think_s: float = 1.3  # median pause between actions
    key_ms: float = 150.0  # median gap between keystrokes
    paste_credentials: bool = False  # password manager
    keyboard_only: bool = False  # screen reader / keyboard navigation
    center_clicks: bool = False  # voice control clicks the exact centre of a target
    dictates: bool = False  # text arrives a word at a time, with no keystrokes
    touch: bool = False  # phone: taps with a finger, there is no cursor at all
    returning_only: bool = False
    new_device: bool = False
    detour: float = 0.55  # chance of wandering to a page that is not on the way


PERSONAS = (
    Persona("padrao", 0.34),
    Persona("celular", 0.20, ip=ip_mobile_cgnat, touch=True, think_s=1.7, key_ms=240),
    Persona("movel_cgnat", 0.15, ip=ip_mobile_cgnat, think_s=1.6, key_ms=210),
    Persona("gerenciador_senhas", 0.12, paste_credentials=True),
    Persona("viajante", 0.06, ip=ip_abroad, returning_only=True),
    Persona("troca_aparelho", 0.07, returning_only=True, new_device=True),
    Persona("leitor_tela", 0.09, keyboard_only=True, think_s=2.2, detour=0.3),
    Persona("controle_voz", 0.09, center_clicks=True, dictates=True, think_s=2.6, detour=0.3),
)


@dataclass(slots=True)
class World:
    """What the lab remembers between sessions: who has an account, on which device."""

    accounts: list[tuple[Identity, list[dict[str, Any]]]] = field(default_factory=list)

    def victims(self) -> list[str]:
        """E-mails an attacker could plausibly hold from a leaked list."""
        return [ident.email for ident, _ in self.accounts]


def pick_persona(rng: random.Random, world: World, touch_share: float | None = None) -> Persona:
    if touch_share is not None and rng.random() < touch_share:
        return next(p for p in PERSONAS if p.touch)
    pool = [p for p in PERSONAS if world.accounts or not p.returning_only]
    return rng.choices(pool, weights=[p.peso for p in pool])[0]


def actor_for(persona: Persona, cenario: str) -> Actor:
    return Actor("humano", 0, persona.nome, cenario, "navegador", False, False)


class Human:
    def __init__(self, page: Any, rng: random.Random, persona: Persona, strokes: list[Stroke]):
        self.page, self.rng, self.p, self.strokes = page, rng, persona, strokes
        self.pos = (rng.uniform(200, 900), rng.uniform(150, 500))

    def _lognormal(self, median: float, sigma: float, cap: float) -> float:
        return min(self.rng.lognormvariate(math.log(median), sigma), cap)

    async def think(self, scale: float = 1.0) -> None:
        await asyncio.sleep(self._lognormal(self.p.think_s * scale, 0.6, 12.0))

    async def _target(self, loc: Any) -> tuple[float, float]:
        await loc.scroll_into_view_if_needed()
        box = await loc.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        if self.p.center_clicks:
            return cx, cy
        # people aim at the middle of a target but land around it
        fx = max(-0.42, min(0.42, self.rng.gauss(0, 0.2)))
        fy = max(-0.42, min(0.42, self.rng.gauss(0, 0.2)))
        return cx + fx * box["width"], cy + fy * box["height"]

    async def _move(self, to: tuple[float, float]) -> None:
        for delay, x, y in path_between(self.rng.choice(self.strokes), self.pos, to):
            await self.page.mouse.move(x, y)
            await asyncio.sleep(delay)
        self.pos = to

    async def click(self, loc: Any) -> None:
        if self.p.keyboard_only:
            await loc.focus()
            await asyncio.sleep(self.rng.uniform(0.15, 0.5))
            await self.page.keyboard.press("Enter")
            return
        if self.p.touch:
            x, y = await self._target(loc)
            await asyncio.sleep(self.rng.uniform(0.15, 0.6))  # the thumb travelling
            await self.page.touchscreen.tap(x, y)
            return
        await self._move(await self._target(loc))
        await asyncio.sleep(self.rng.uniform(0.05, 0.2))
        await self.page.mouse.down()
        await asyncio.sleep(self.rng.uniform(0.04, 0.13))
        await self.page.mouse.up()

    async def _go(self, action: Any) -> None:
        """Perform an action that loads a new page, and wait for *that* page.
        (Waiting for the load state alone returns at once: the old page is loaded.)"""
        try:
            async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=15_000):
                await action
        except Exception as exc:
            if "Timeout" not in type(exc).__name__:
                raise  # no navigation (e.g. the browser refused an invalid form) is fine

    async def focus(self, loc: Any) -> None:
        if self.p.keyboard_only:
            await loc.focus()
        else:
            await self.click(loc)
        await asyncio.sleep(self.rng.uniform(0.15, 0.6))

    async def write(self, loc: Any, text: str, *, credential: bool = False) -> None:
        if not text:
            return
        await self.focus(loc)
        if credential and self.p.paste_credentials:
            await loc.fill(text)  # the manager sets the value: no keystrokes
        elif self.p.dictates:
            for word in text.split(" "):
                await self.page.keyboard.insert_text(word + " ")
                await asyncio.sleep(self.rng.uniform(0.25, 0.9))
        else:
            for ch in text:
                await self.page.keyboard.type(ch)
                gap = self._lognormal(self.p.key_ms, 0.5, 2500) / 1000
                if self.rng.random() < 0.04:
                    gap += self.rng.uniform(0.4, 1.5)  # looking for a key, thinking
                await asyncio.sleep(gap)
        await asyncio.sleep(self.rng.uniform(0.2, 0.7))

    async def submit(self, form: str) -> None:
        if self.p.keyboard_only:
            await self._go(self.page.keyboard.press("Enter"))
        else:
            await self._go(self.click(self.page.locator(f"{form} button[type=submit]")))

    async def nav(self, href: str) -> None:
        await self._go(self.click(self.page.locator(f'nav a[href="{href}"]').first))
        await self.think()

    # ------------------------------------------------------------- behaviours

    async def wander(self) -> None:
        """Humans get distracted; an agent on a mission does not."""
        for _ in range(self.rng.choice((1, 1, 2, 3))):
            if self.rng.random() < 0.7:
                links = self.page.locator('a[href^="/produto/"]')
                n = await links.count()
                if n:
                    await self._go(self.click(links.nth(self.rng.randrange(n))))
                    await self.think(1.6)
                    await self.nav("/")
            else:
                await self.nav("/sobre")
                await self.nav("/")

    async def signup(self, ident: Identity) -> None:
        await self.nav("/cadastro")
        f = 'form[action="/cadastro"]'
        await self.write(self.page.locator(f"{f} [name=nome]"), ident.nome)
        await self.write(self.page.locator(f"{f} [name=email]"), ident.email, credential=True)
        await self.write(self.page.locator(f"{f} [name=senha]"), ident.senha, credential=True)
        if self.rng.random() < 0.8:
            await self.write(self.page.locator(f"{f} [name=endereco]"), ident.endereco)
        bio = ident.bio
        if self.p.keyboard_only and self.rng.random() < 0.2:
            # A screen-reader user who met the hidden field and the hidden instruction,
            # and did what they said. This must never be enough to block anyone.
            await self.page.locator(f"{f} [name=website]").evaluate(
                "e => e.value = 'meusite.example'"
            )
            bio = (bio + " lumen-7").strip()
        if bio:
            await self.write(self.page.locator(f"{f} [name=bio]"), bio)
        await self.think(0.6)
        await self.submit(f)

    async def login(self, ident: Identity) -> None:
        await self.nav("/login")
        f = 'form[action="/login"]'
        attempts = (
            [ident.senha[:-1] + "x", ident.senha] if self.rng.random() < 0.1 else [ident.senha]
        )
        for senha in attempts:  # one in ten mistypes the password first
            await self.write(self.page.locator(f"{f} [name=email]"), ident.email, credential=True)
            await self.write(self.page.locator(f"{f} [name=senha]"), senha, credential=True)
            await self.submit(f)
            await self.think(0.5)

    async def buy(self, *, coupon: bool) -> None:
        for _ in range(self.rng.choice((1, 1, 1, 2))):
            buttons = self.page.locator('form[action="/carrinho/adicionar"] button')
            if not await buttons.count():  # not on a page that sells anything
                await self.page.goto("/")
                await self.think()
            await self._go(self.click(buttons.nth(self.rng.randrange(await buttons.count()))))
            await self.think()
            await self.nav("/")
        await self.nav("/carrinho")
        await self._go(self.click(self.page.locator('a[href="/checkout"]').last))
        await self.think()
        f = 'form[action="/checkout"]'
        if not await self.page.locator(f).count():
            return
        await self.write(
            self.page.locator(f"{f} [name=cartao]"), test_card(self.rng), credential=True
        )
        if coupon:
            await self.write(self.page.locator(f"{f} [name=cupom]"), "PRIMEIRA10")
        await self.submit(f)
        await self.think(0.5)


async def run_human(
    page: Any, rng: random.Random, persona: Persona, strokes: list[Stroke], known: Identity | None
) -> Identity | None:
    """One visit. ``known`` is the visitor's existing account, if they have one.
    Returns the identity of a newly created account."""
    h = Human(page, rng, persona, strokes)
    await page.goto("/")
    await h.think()
    if rng.random() < persona.detour:
        await h.wander()
    if known:
        await h.login(known)
        if rng.random() < 0.5:
            await h.buy(coupon=False)
        elif rng.random() < 0.5:
            await h.wander()
        return None
    ident = identity(rng)
    await h.signup(ident)
    if rng.random() < 0.7:
        await h.buy(coupon=rng.random() < 0.8)
    return ident
