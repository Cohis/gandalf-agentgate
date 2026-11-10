"""Shared plumbing for the lab's traffic generators (humans and attackers alike)."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

SHOP_URL = os.environ.get("LAB_SHOP_URL", "https://localhost:8443")
RUNS_DIR = Path("data/runs")
_LOCAL = {"localhost", "127.0.0.1", "::1", "proxy", "shop"}


def guard(url: str) -> str:
    """Every generator calls this first. Attacks only ever target the lab shop."""
    host = urlparse(url).hostname
    if host not in _LOCAL:
        raise SystemExit(f"refusing target '{host}': this code may only be aimed at the local lab")
    return url


@dataclass(frozen=True, slots=True)
class Actor:
    """Ground truth for one session. Never sent to the shop or the engine with the traffic."""

    ator_real: str  # humano | automacao_nao_declarada | agente_declarado
    nivel: int  # 0 human, 1..5 attacker ladder
    perfil: str  # persona or tool
    cenario: str  # what the session is doing
    driver: str  # how the traffic was produced (navegador | http)
    eh_automacao: bool
    eh_fraude: bool


class Truth:
    """Append-only answer key, kept on disk away from the engine's database."""

    def __init__(self, run: str) -> None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = RUNS_DIR / f"{run}.jsonl"
        self.count = 0

    def record(self, avaliacao_id: str, decisao: str, actor: Actor, sessao: str) -> None:
        row = {"avaliacao_id": avaliacao_id, "decisao": decisao, "sessao": sessao, **asdict(actor)}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.count += 1


class Clock:
    """Simulated time = real time + an offset that only ever jumps forward.

    Sessions in one batch share the offset, so event time stays in processing order
    (the engine's windows and the offline replay both rely on that).
    """

    def __init__(self, days: float) -> None:
        self.offset_s = -days * 86_400.0
        self._t0 = time.monotonic()

    def jump(self, seconds: float) -> None:
        self.offset_s = min(self.offset_s + seconds, 0.0)

    def header(self) -> str:
        return f"{self.offset_s:.0f}"


# Fictional lab addresses. Until phase 6 the engine does not classify networks, so
# these only drive the per-IP velocity windows.
def ip_residential(rng: random.Random) -> str:
    return f"{rng.choice((177, 187, 189, 191, 200))}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"  # noqa: E501


_CGNAT = [f"179.240.{a}.{b}" for a in range(3) for b in range(1, 11)]  # 30 shared exits


def ip_mobile_cgnat(rng: random.Random) -> str:
    return rng.choice(_CGNAT)  # thousands of people behind a handful of addresses


def ip_datacenter(rng: random.Random) -> str:
    return f"{rng.choice((34, 35, 52, 54, 104))}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"  # noqa: E501


def ip_abroad(rng: random.Random) -> str:
    return f"{rng.choice((81, 85, 93, 151))}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"  # noqa: E501


def test_card(rng: random.Random, *, valid: bool = True) -> str:
    """A random 16-digit number that passes (or deliberately fails) the Luhn check."""
    digits = [4] + [rng.randrange(10) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    check = (10 - total % 10) % 10
    if not valid:
        check = (check + rng.randrange(1, 10)) % 10
    return "".join(map(str, [*digits, check]))


_FIRST = (
    "Ana Bruno Carla Diego Elisa Fábio Gabriela Heitor Isabela João "
    "Karina Lucas Marina Nuno Olívia Paulo Rafaela Sérgio Tatiana Vitor"
).split()
_LAST = (
    "Almeida Barbosa Cardoso Dias Esteves Ferreira Gomes Lima "
    "Martins Nogueira Oliveira Pereira Ramos Souza Teixeira Vieira"
).split()
_STREETS = (
    "das Flores",
    "São João",
    "Sete de Setembro",
    "da Praia",
    "Quinze de Novembro",
    "do Sol",
    "Bahia",
    "das Acácias",
)
_BIOS = [
    "",
    "",
    "Gosto de trilhas e café.",
    "Comprando presente pro meu irmão.",
    "professora, mãe de dois",
    "Colecionador de canecas desde 2015!",
    "vi a loja no instagram",
]


_PW_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


@dataclass(frozen=True, slots=True)
class Identity:
    nome: str
    email: str
    senha: str
    endereco: str
    bio: str


def identity(rng: random.Random) -> Identity:
    first, last = rng.choice(_FIRST), rng.choice(_LAST)
    slug = f"{first}.{last}".lower().encode("ascii", "ignore").decode() or "cliente"
    return Identity(
        nome=f"{first} {last}",
        email=f"{slug}{rng.randrange(10_000)}@example.com",
        senha="".join(rng.choice(_PW_CHARS) for _ in range(rng.randint(8, 12))),
        endereco=f"Rua {rng.choice(_STREETS)}, {rng.randrange(1, 2000)}",
        bio=rng.choice(_BIOS),
    )
