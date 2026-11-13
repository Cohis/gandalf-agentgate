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


# Lab addresses. When the IP tables exist (jobs/ip_reputation.py), each generator
# samples from real ranges of the right kind, so the engine's enrichment sees a
# consistent network type: a person at home on a Brazilian ISP, a phone on a mobile
# operator, a script on a cloud provider. The addresses are still never contacted.
_POOLS: dict[str, list[tuple[int, int]]] | None = None


def _pools() -> dict[str, list[tuple[int, int]]]:
    global _POOLS
    if _POOLS is None:
        _POOLS = {}
        try:
            import numpy as np

            with np.load("data/ip/rede.npz") as t:
                tipos = [str(x) for x in t["tipos"]]
                kind = np.asarray([tipos[i] for i in t["tipo"]])
                big = (t["asn_end"].astype(np.int64) - t["asn_start"]) >= 255
                for name, mask in {
                    "br_residencial": (kind == "residencial") & (t["pais"] == "BR") & big,
                    "br_movel": (kind == "movel") & big,
                    "datacenter": (kind == "datacenter") & big,
                    "exterior": (kind == "residencial")
                    & (t["pais"] != "BR")
                    & (t["pais"] != "")
                    & big,
                }.items():
                    _POOLS[name] = list(
                        zip(t["asn_start"][mask].tolist(), t["asn_end"][mask].tolist(), strict=True)
                    )
                _POOLS["_lista"] = list(
                    zip(t["lista_start"].tolist(), t["lista_end"].tolist(), strict=True)
                )
        except (OSError, KeyError):
            pass
    return _POOLS


def _listed(n: int) -> bool:
    import bisect

    lst = _pools().get("_lista", [])
    i = bisect.bisect_right(lst, (n, 1 << 32)) - 1
    return i >= 0 and lst[i][0] <= n <= lst[i][1]


def _sample(rng: random.Random, pool: str, fallback: str) -> str:
    """An address from a real range of the right kind, never from a public blocklist
    (honest traffic and careful attackers do not come from hijacked netblocks)."""
    ranges = _pools().get(pool)
    if not ranges:
        return fallback
    for _ in range(20):
        a, b = rng.choice(ranges)
        n = rng.randint(a + 1, b - 1)
        if not _listed(n):
            break
    return f"{n >> 24 & 255}.{n >> 16 & 255}.{n >> 8 & 255}.{n & 255}"


def _rand(rng: random.Random, firsts: tuple[int, ...]) -> str:
    return f"{rng.choice(firsts)}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"


def ip_residential(rng: random.Random) -> str:
    return _sample(rng, "br_residencial", _rand(rng, (177, 187, 189, 191, 200)))


_CGNAT: list[str] = []


def ip_mobile_cgnat(rng: random.Random) -> str:
    """Thousands of people behind a handful of carrier-grade NAT exits."""
    if not _CGNAT:
        seed = random.Random(26615)
        _CGNAT.extend(
            _sample(seed, "br_movel", f"179.240.{i // 10}.{i % 10 + 1}") for i in range(30)
        )
    return rng.choice(_CGNAT)


def ip_datacenter(rng: random.Random) -> str:
    return _sample(rng, "datacenter", _rand(rng, (34, 35, 52, 54, 104)))


def ip_abroad(rng: random.Random) -> str:
    return _sample(rng, "exterior", _rand(rng, (81, 85, 93, 151)))


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


# Identities of simulated honest people. Deliberately varied: if every simulated
# person wrote the same kind of e-mail and bio, the sign-up similarity layer (phase
# 4) would learn the generator instead of the account farm.
_FIRST = (
    "Ana Bruno Carla Diego Elisa Fábio Gabriela Heitor Isabela João Karina Lucas Marina "
    "Nuno Olívia Paulo Rafaela Sérgio Tatiana Vitor Antônia Benedito Cleide Edson Francisca "
    "Geraldo Iracema Jéssica Kauã Luzia Matheus Neide Otávio Priscila Raimundo Sueli "
    "Thiago Valdirene Wellington Yasmin Zuleica Arthur Beatriz Caio Daniela Enzo Fernanda "
    "Gustavo Helena Igor Juliana Kátia Leonardo Manuela Natália Rogério Simone Ubirajara"
).split()
_LAST = (
    "Almeida Barbosa Cardoso Dias Esteves Ferreira Gomes Lima Martins Nogueira Oliveira "
    "Pereira Ramos Souza Teixeira Vieira Silva Santos Rodrigues Costa Ribeiro Carvalho "
    "Araújo Rocha Mendes Moreira Nascimento Freitas Moura Cavalcanti Monteiro Batista "
    "Correia Pinto Teles Brandão Siqueira Queiroz Macedo Farias Coelho"
).split()
_STREETS = (
    "Rua das Flores", "R. São João", "Av. Sete de Setembro", "Rua da Praia",
    "R. Quinze de Novembro", "Travessa do Sol", "Av. Bahia", "Rua das Acácias",
    "Alameda Santos", "Rua Tiradentes", "Av. Brasil", "R. Dom Pedro II", "Rua Goiás",
    "Estrada do Campo Limpo", "Rua Padre Anchieta", "Av. Getúlio Vargas",
)  # fmt: skip
_COMPLEMENTS = ("", "", "", " apto 12", " ap 304", " casa 2", " bloco B", " fundos")
_DOMAINS = ("gmail.com",) * 6 + (
    "hotmail.com",
    "outlook.com",
    "yahoo.com.br",
    "uol.com.br",
    "bol.com.br",
)
_BIO_BITS = (
    "gosto de cozinhar", "trabalho com vendas", "estudante de enfermagem", "mãe do Pedro",
    "aposentado", "fã de café", "comprando presente", "vi no insta", "moro em Curitiba",
    "corredora amadora", "pai de pet", "professor de história", "adoro viajar",
    "primeira compra aqui", "indicação de amiga", "colecionando canecas", "sou de BH",
    "faço artesanato", "trabalho de casa", "curto trilha",
)  # fmt: skip


def _ascii(text: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    ).lower()


def _email(rng: random.Random, first: str, last: str) -> str:
    f, l_ = _ascii(first), _ascii(last)
    year = rng.randrange(1960, 2008)
    local = rng.choice(
        (
            f"{f}.{l_}",
            f"{f}{l_}",
            f"{f}_{l_}{rng.randrange(10, 99)}",
            f"{f[0]}{l_}{year % 100:02d}",
            f"{f}{year}",
            f"{l_}.{f}",
            f"{f}{rng.randrange(1, 999)}",
            f"{f[:3]}{l_[:4]}",
        )
    )
    return f"{local}@{rng.choice(_DOMAINS)}"


def _bio(rng: random.Random) -> str:
    if rng.random() < 0.65:
        return ""  # most people skip optional fields
    bits = rng.sample(_BIO_BITS, rng.choice((1, 1, 2)))
    text = ", ".join(bits)
    if rng.random() < 0.5:
        text = text.capitalize()
    return text + rng.choice(("", "", ".", "!", " :)"))


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
    if rng.random() < 0.3:
        last = f"{last} {rng.choice(_LAST)}"
    endereco = (
        ""
        if rng.random() < 0.15
        else (
            f"{rng.choice(_STREETS)}{rng.choice((', ', ' ', ', nº '))}{rng.randrange(1, 3000)}"
            f"{rng.choice(_COMPLEMENTS)}"
        )
    )
    return Identity(
        nome=f"{first} {last}" if rng.random() < 0.9 else first.lower(),
        email=_email(rng, first, last.split()[0]),
        senha="".join(rng.choice(_PW_CHARS) for _ in range(rng.randint(8, 12))),
        endereco=endereco,
        bio=_bio(rng),
    )
