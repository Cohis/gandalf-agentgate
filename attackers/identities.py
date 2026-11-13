"""Synthetic identities for the level 4 account farm, written by a local LLM.

    uv run python -m attackers.identities --farm A --count 120

This is what a farm operator does: ask a language model for a batch of plausible
customer profiles. Two farms use differently worded requests, so the evaluation can
train on one and test on the other. The output is cached in data/ (not committed).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import unicodedata
from pathlib import Path

import httpx

from simulator.common import guard

LLM_URL = os.environ.get("LAB_LLM_URL", "http://127.0.0.1:8080/v1")
OUT = Path("data")

PROMPTS = {
    "A": (
        "Gere {n} perfis fictícios de clientes brasileiros de uma loja online. Para cada um: "
        "nome completo, e-mail, endereço de entrega (rua, número e bairro) e uma bio curta "
        "de uma frase sobre gostos pessoais. Varie idades, cidades e profissões."
    ),
    "B": (
        "Preciso de {n} cadastros realistas de compradores do Brasil para testes. Cada "
        "cadastro deve ter nome, email, endereço completo e uma pequena descrição da "
        "pessoa, como ela se apresentaria numa rede social. Seja criativo e natural."
    ),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "perfis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "email": {"type": "string"},
                    "endereco": {"type": "string"},
                    "bio": {"type": "string"},
                },
                "required": ["nome", "email", "endereco", "bio"],
            },
        }
    },
    "required": ["perfis"],
}


def _clean_email(email: str, nome: str, rng: random.Random) -> str:
    email = unicodedata.normalize("NFD", email.strip().lower())
    email = "".join(c for c in email if c.isascii() and (c.isalnum() or c in "._@-"))
    if email.count("@") != 1 or "." not in email.split("@")[1]:
        base = "".join(c for c in unicodedata.normalize("NFD", nome.lower()) if c.isalpha())
        email = f"{base[:12] or 'cliente'}{rng.randrange(100, 999)}@gmail.com"
    return email


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--farm", choices=sorted(PROMPTS), default="A")
    ap.add_argument("--count", type=int, default=120)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    guard(LLM_URL)  # local model only

    rng = random.Random(a.farm)
    out = OUT / f"identidades_llm_{a.farm}.json"
    profiles: list[dict[str, str]] = json.loads(out.read_text()) if out.exists() else []
    seen = {p["email"] for p in profiles}
    started = time.monotonic()
    with httpx.Client(base_url=LLM_URL, timeout=600) as llm:
        while len(profiles) < a.count:
            r = llm.post(
                "/chat/completions",
                json={
                    "model": "qwen3.5-9b",
                    "messages": [{"role": "user", "content": PROMPTS[a.farm].format(n=a.batch)}],
                    "max_tokens": 1400,
                    "temperature": 0.9,
                    "seed": rng.randrange(1 << 30),
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "perfis", "schema": SCHEMA},
                    },
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            r.raise_for_status()
            try:
                batch = json.loads(r.json()["choices"][0]["message"]["content"])["perfis"]
            except (ValueError, KeyError):
                continue
            for p in batch:
                p = {
                    k: str(p.get(k, "")).strip()[:300] for k in ("nome", "email", "endereco", "bio")
                }
                p["email"] = _clean_email(p["email"], p["nome"], rng)
                if p["nome"] and p["email"] not in seen:
                    seen.add(p["email"])
                    profiles.append(p)
            out.write_text(json.dumps(profiles, ensure_ascii=False, indent=1))
            print(
                f"[{time.monotonic() - started:5.0f}s] fazenda {a.farm}: {len(profiles)}/{a.count}",
                flush=True,
            )
    print(f"salvo em {out}")


if __name__ == "__main__":
    main()
