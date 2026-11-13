"""Attacker level 4: an account farm built to pass as people. Lab use only.

Every session is one new account and is made to look human on its own:
- pointer paths replayed from human recordings, clicks spread over the target,
  human typing cadence (the same driver as the simulated people);
- a fresh device and a residential or mobile address for each account;
- detours through the catalogue before signing up;
- identities written by a local LLM (attackers/identities.py), not a template.

What it cannot hide is scale: dozens of sign-ups in a few hours, written by the
same model. That is what the phase 4 layer is supposed to see.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from simulator.common import Actor, Identity, Truth, ip_mobile_cgnat, ip_residential, test_card
from simulator.fraud import CHEAP, Fraudster
from simulator.human import PERSONAS
from simulator.strokes import Stroke

COUPON_ITEMS = (CHEAP, "p3", "p1", "p11", "p12")


def _password() -> str:
    return "".join(random.sample("abcdefghjkmnpqrstuvwxyz23456789", 10))


def load_pool(farm: str) -> list[Identity]:
    path = Path(f"data/identidades_llm_{farm}.json")
    if not path.exists():
        raise SystemExit(f"no identities for farm {farm}: run attackers.identities --farm {farm}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Identity(r["nome"], r["email"], _password(), r["endereco"], r["bio"]) for r in rows]


def actor(farm: str) -> Actor:
    return Actor(
        "automacao_nao_declarada", 4, f"fazenda_llm_{farm}", "fazenda_contas", "navegador",
        True, True,
    )  # fmt: skip


async def run_farm_account(
    page: Any, rng: random.Random, strokes: list[Stroke], ident: Identity, touch: bool
) -> None:
    persona = next(p for p in PERSONAS if p.nome == ("celular" if touch else "padrao"))
    f = Fraudster(page, rng, persona, strokes)
    await page.goto("/")
    await f.think()
    if rng.random() < 0.6:
        await f.wander()  # a person browses first; so does a careful farm
    await f.signup(ident)
    await f.add(rng.choice(COUPON_ITEMS))
    await f.pay(test_card(rng), coupon=True)


def farm_ip(rng: random.Random) -> str:
    return ip_mobile_cgnat(rng) if rng.random() < 0.3 else ip_residential(rng)


__all__ = ["Truth", "actor", "farm_ip", "load_pool", "run_farm_account"]
