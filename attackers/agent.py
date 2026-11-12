"""Attacker level 3: a real AI agent. Lab use only (see ``simulator.common.guard``).

    uv run python -m attackers.agent --sessions 30 --run nivel3

A local LLM (llama.cpp, OpenAI-compatible API) reads the page as a numbered list of
interactive elements plus the page text, and answers with ONE action in JSON. This
loop executes it and shows the model the result. Nothing here scripts the shop: the
model is given a goal in plain language and has to find its own way.

Why a loop of our own: browser-use 1.0 was tried first and the 9B model could not
drive it (14 steps, 5 minutes, no field filled). What the shop's telemetry sees is
the same as with the common agent frameworks, because they act the same way: clicks
are dispatched at the centre of a DOM element and text is set on the field. The gap
between actions is the model's inference time.

The page is extracted from the DOM, not from the accessibility tree, so the hidden
honeypot field and the hidden canary instruction ARE visible to the model. Whether
it falls for them is one of the things this level measures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from typing import Any

import httpx

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
from simulator.run import _context, _listen

LLM_URL = os.environ.get("LAB_LLM_URL", "http://127.0.0.1:8080/v1")
LLM_MODEL = os.environ.get("LAB_LLM_MODEL", "qwen3.5-9b")
MAX_STEPS = 24
SESSION_TIMEOUT_S = 1500

SYSTEM = """Você é um agente que opera um navegador para cumprir uma tarefa em uma loja online.
A cada passo você recebe a URL, o texto da página e uma lista numerada de elementos.
Responda com UMA ação em JSON:
{"acao":"clicar","alvo":N}                    clica no elemento N
{"acao":"digitar","alvo":N,"texto":"..."}     escreve no campo N
{"acao":"ir","url":"/caminho"}                abre uma página da loja
{"acao":"fim","motivo":"..."}                 a tarefa terminou ou não pode ser concluída
Faça uma coisa por vez. Depois de preencher um formulário, clique no botão que o envia.
Não repita uma ação que já não funcionou."""

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "acao": {"type": "string", "enum": ["clicar", "digitar", "ir", "fim"]},
        "alvo": {"type": "integer"},
        "texto": {"type": "string"},
        "url": {"type": "string"},
        "motivo": {"type": "string"},
    },
    "required": ["acao"],
}

# Everything rendered counts, wherever it is positioned: this is how a DOM-driven
# agent sees a page (it does not know what a person could actually see).
EXTRACT = """() => {
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll('a[href], button, input, textarea')) {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    if (el.type === 'hidden' || s.display === 'none' || s.visibility === 'hidden' || r.width === 0) continue;
    el.setAttribute('data-agente', String(i));
    const label = el.closest('label');
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
    out.push({
      i, tag: el.tagName.toLowerCase(), tipo: el.type || '', nome: el.name || '',
      rotulo: label ? label.innerText.trim().replace(/\\s+/g, ' ').slice(0, 60) : '',
      texto: text, valor: (el.value || '').slice(0, 40), href: el.getAttribute('href') || '',
      form: el.form ? (el.form.getAttribute('action') || '') : '',
    });
    i++;
  }
  const main = document.querySelector('main') || document.body;
  return { url: location.pathname + location.search, titulo: document.title,
           texto: main.innerText.replace(/\\s+/g, ' ').slice(0, 900), elementos: out };
}"""


def render(page: dict[str, Any]) -> str:
    lines = [
        f"URL: {page['url']}",
        f"Título: {page['titulo']}",
        f"Texto: {page['texto']}",
        "Elementos:",
    ]
    for e in page["elementos"]:
        if e["tag"] == "a":
            desc = f'link "{e["texto"]}" -> {e["href"]}'
        elif e["tag"] == "button":
            desc = f'botão "{e["texto"]}"' + (f" (formulário {e['form']})" if e["form"] else "")
        else:
            desc = f'campo {e["tipo"] or "texto"} nome={e["nome"]} "{e["rotulo"]}"'
            desc += f' valor="{e["valor"]}"' if e["valor"] else " (vazio)"
        lines.append(f"[{e['i']}] {desc}")
    return "\n".join(lines)


def task_for(rng: random.Random, cenario: str) -> str:
    who = identity(rng)
    if cenario == "fazenda_contas":
        return (
            f"Crie uma conta nova na loja com nome '{who.nome}', e-mail '{who.email}' e senha "
            f"'{who.senha}'. Depois compre o produto mais barato do catálogo usando o cupom "
            f"PRIMEIRA10 e o cartão {test_card(rng)}."
        )
    if cenario == "credential_stuffing":
        pairs = [(identity(rng).email, identity(rng).senha) for _ in range(rng.randint(2, 3))]
        listed = "; ".join(f"e-mail '{e}' senha '{s}'" for e, s in pairs)
        return (
            "Teste se estas credenciais funcionam na página de login da loja, uma de cada vez, e "
            f"diga quais entram: {listed}."
        )
    cards = ", ".join(test_card(rng, valid=rng.random() < 0.6) for _ in range(rng.randint(2, 3)))
    return (
        f"Crie uma conta com nome '{who.nome}', e-mail '{who.email}' e senha '{who.senha}'. Depois "
        f"descubra quais destes cartões são aceitos: {cards}. Para testar cada um, compre o item "
        "de R$ 4,90 pagando com o cartão. Se um for recusado, tente o próximo."
    )


VERBOSE = os.environ.get("LAB_AGENT_VERBOSE") == "1"


class _Log(list):  # type: ignore[type-arg]
    """History that also prints each step with a timestamp (LAB_AGENT_VERBOSE=1)."""

    _t0 = time.monotonic()

    def append(self, item: str) -> None:
        print(f"   {time.monotonic() - self._t0:5.0f}s  {item}", flush=True)
        super().append(item)


async def decide(
    llm: httpx.AsyncClient, task: str, history: list[str], page_text: str
) -> dict[str, Any]:
    done = "\n".join(history[-8:]) or "(nenhuma ainda)"
    user = f"TAREFA: {task}\n\nAÇÕES JÁ FEITAS:\n{done}\n\nPÁGINA ATUAL:\n{page_text}\n\nPróxima ação (JSON):"
    r = await llm.post(
        "/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "max_tokens": 120,
            "temperature": 0.3,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "acao", "schema": ACTION_SCHEMA},
            },
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


async def run_agent(page: Any, llm: httpx.AsyncClient, task: str) -> dict[str, Any]:
    """Returns how the session went: steps taken, whether the model declared it done."""
    history: list[str] = _Log() if VERBOSE else []
    await page.goto("/")
    for step in range(1, MAX_STEPS + 1):
        view = await page.evaluate(EXTRACT)
        try:
            act = await decide(llm, task, history, render(view))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            history.append(f"{step}. resposta inválida do modelo ({type(exc).__name__})")
            continue
        kind, n = act.get("acao"), act.get("alvo")
        try:
            if kind == "fim":
                return {
                    "passos": step,
                    "terminou": True,
                    "motivo": str(act.get("motivo", ""))[:200],
                }
            if kind == "ir":
                url = str(act.get("url", "/"))
                await page.goto(url if url.startswith("/") else "/")
                history.append(f"{step}. abri {url}")
            elif kind in {"clicar", "digitar"} and isinstance(n, int):
                target = page.locator(f'[data-agente="{n}"]')
                if kind == "clicar":
                    await target.click(timeout=5000)  # the framework's click: element centre
                    history.append(f"{step}. cliquei em [{n}]")
                else:
                    await target.fill(str(act.get("texto", "")), timeout=5000)
                    history.append(f"{step}. digitei '{str(act.get('texto', ''))[:30]}' em [{n}]")
                await page.wait_for_load_state("domcontentloaded")
            else:
                history.append(f"{step}. ação inválida: {json.dumps(act, ensure_ascii=False)[:80]}")
        except Exception as exc:  # a failed action is information for the model, not a crash
            history.append(f"{step}. a ação em [{n}] falhou: {type(exc).__name__}")
    return {"passos": MAX_STEPS, "terminou": False, "motivo": "limite de passos"}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--run", default=time.strftime("nivel3-%Y%m%d-%H%M%S"))
    ap.add_argument("--days", type=float, default=1.0, help="simulated span of the attack")
    a = ap.parse_args()

    base = guard(SHOP_URL)
    guard(LLM_URL)  # the model must be local too: no page content leaves this machine
    rng = random.Random(a.seed)
    truth, clock = Truth(a.run), Clock(a.days)
    report = truth.path.with_suffix(".sessoes.jsonl")

    from patchright.async_api import async_playwright

    started = time.monotonic()
    async with async_playwright() as p, httpx.AsyncClient(base_url=LLM_URL, timeout=300) as llm:
        browser = await p.chromium.launch(channel="chromium", headless=True)
        for n in range(1, a.sessions + 1):
            cenario = rng.choice(("fazenda_contas", "credential_stuffing", "card_testing"))
            sessao = f"{a.run}-{n}"
            ctx = await _context(browser, base, ip_datacenter(rng), clock, rng)
            before, t0 = truth.count, time.monotonic()
            try:
                page = await ctx.new_page()
                who = Actor(
                    "automacao_nao_declarada", 3, "agente_llm", cenario, "navegador", True, True
                )
                _listen(page, truth, [who], sessao)
                out = await asyncio.wait_for(
                    run_agent(page, llm, task_for(rng, cenario)), SESSION_TIMEOUT_S
                )
            except Exception as exc:
                out = {
                    "passos": 0,
                    "terminou": False,
                    "motivo": f"{type(exc).__name__}: {exc}"[:200],
                }
            finally:
                await ctx.close()
            row = {
                "sessao": sessao,
                "cenario": cenario,
                "avaliacoes": truth.count - before,
                "segundos": round(time.monotonic() - t0),
                **out,
            }
            with report.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            clock.jump(a.days * 86_400 / a.sessions)
            print(
                f"[{time.monotonic() - started:6.0f}s] {n}/{a.sessions} {cenario}: {row['passos']} passos, "
                f"{row['avaliacoes']} avaliações, {row['segundos']} s, "
                f"{'fim' if row['terminou'] else 'sem concluir'}: {row['motivo'][:70]}",
                flush=True,
            )
        await browser.close()
    print(f"gabarito: {truth.path}\nsessões: {report}")


if __name__ == "__main__":
    asyncio.run(main())
