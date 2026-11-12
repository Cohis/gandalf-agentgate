"""Human fraudsters: real-looking people (recorded mouse paths, human typing) who
commit fraud by hand. The automation model should leave them alone; catching them
is the fraud model's job, from what they do rather than how they move.

Labels are set per action: only the fraudulent steps are marked as fraud, so the
fraudster's ordinary first sign-up, for example, stays a legitimate event.

    cupom_multicontas    one device, several new accounts, the first-purchase coupon each time
    teste_cartao_manual  one account, several cards tried on a cheap item, most refused
    conta_roubada        a leaked e-mail and password used from another device and address,
                         then an expensive purchase
"""

from __future__ import annotations

import random
from typing import Any

from .common import Actor, Identity, identity, test_card
from .human import Human, Persona, World
from .strokes import Stroke

SCENARIOS = ("cupom_multicontas", "teste_cartao_manual", "conta_roubada")
EXPENSIVE = ("p5", "p2", "p7")
CHEAP = "p6"


def actors(cenario: str) -> tuple[Actor, Actor]:
    perfil = f"fraude_{cenario}"
    benign = Actor("humano", 0, perfil, cenario, "navegador", False, False)
    fraud = Actor("humano", 0, perfil, cenario, "navegador", False, True)
    return benign, fraud


class Fraudster(Human):
    async def logout(self) -> None:
        button = self.page.locator('form[action="/sair"] button')
        if await button.count():
            await self._go(self.click(button.first))
            await self.think(0.5)

    async def add(self, pid: str) -> None:
        await self.page.goto(f"/produto/{pid}")
        await self.think()
        await self._go(self.click(self.page.locator(".caixa-compra button")))
        await self.think(0.6)

    async def pay(self, card: str, *, coupon: bool) -> None:
        await self.nav("/carrinho")
        link = self.page.locator('a[href="/checkout"]')
        if not await link.count():
            return
        await self._go(self.click(link.last))
        await self.think()
        f = 'form[action="/checkout"]'
        if not await self.page.locator(f).count():
            return
        await self.write(self.page.locator(f"{f} [name=cartao]"), card, credential=True)
        if coupon:
            await self.write(self.page.locator(f"{f} [name=cupom]"), "PRIMEIRA10")
        await self.submit(f)
        await self.think(0.5)


async def run_fraudster(
    page: Any,
    rng: random.Random,
    persona: Persona,
    strokes: list[Stroke],
    world: World,
    who: list[Actor],
    cenario: str,
) -> Identity | None:
    """Plays one fraud scenario. ``who[0]`` is switched before each step so every
    assessment is labelled with whether that step was the fraudulent one."""
    if cenario == "conta_roubada" and not world.accounts:
        cenario = "teste_cartao_manual"  # nothing to steal yet
    benign, fraud = actors(cenario)
    f = Fraudster(page, rng, persona, strokes)
    who[0] = benign
    await page.goto("/")
    await f.think()

    if cenario == "conta_roubada" and world.accounts:
        victim, _device = rng.choice(world.accounts)  # a leaked credential pair
        who[0] = fraud
        await f.login(victim)
        for _ in range(rng.choice((1, 2))):
            await f.add(rng.choice(EXPENSIVE))
        await f.pay(test_card(rng), coupon=False)
        return None

    if cenario == "cupom_multicontas":
        first = None
        for n in range(rng.randint(2, 4)):
            who[0] = benign if n == 0 else fraud  # the first account is the honest one
            ident = identity(rng)
            first = first or ident
            await f.signup(ident)
            await f.add(rng.choice((CHEAP, "p3", "p1")))
            await f.pay(test_card(rng), coupon=True)
            await f.logout()
        return first

    # teste_cartao_manual (also the fallback when there is no account to steal yet)
    ident = identity(rng)
    await f.signup(ident)
    who[0] = fraud  # from here on, every step serves the card test
    for _ in range(rng.randint(3, 6)):
        await f.add(CHEAP)
        await f.pay(test_card(rng, valid=rng.random() < 0.35), coupon=False)
    return ident
