"""Generate labelled lab traffic: simulated people plus attacker levels 1 and 2.

    uv run python -m simulator.run --humans 300 --l1 120 --l2 120 --days 6

Sessions run in batches. Each batch shares one simulated clock value, and the clock
only moves forward between batches, so event time matches processing order.

Run the shop in shadow mode (SHOP_SHADOW=1) while collecting data: a blocked
attacker stops at the first step and everything after it would be missing.

The answer key goes to data/runs/<run>.jsonl. Send it to the engine afterwards with
``python -m simulator.labels`` (labels arrive late in real life too).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from typing import Any

from attackers.scripts import SCENARIOS, Script, actor, attacker_ip, l1_http

from .common import SHOP_URL, Clock, Truth, guard
from .human import World, actor_for, pick_persona, run_human
from .strokes import load

VIEWPORTS = ((1366, 768), (1440, 900), (1536, 864), (1920, 1080), (1280, 720))
SESSION_TIMEOUT_S = 240


def _desktop_ua(browser: Any) -> str:
    """Headless Chromium announces itself in the user agent; anything that wants to
    pass for a person (a stealth attacker, or our simulated people) replaces it."""
    major = browser.version.split(".")[0]
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


PHONES = ((390, 844), (393, 873), (412, 915), (360, 800))


def _phone_ua(browser: Any) -> str:
    major = browser.version.split(".")[0]
    return (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Mobile Safari/537.36"
    )


async def _context(
    browser: Any,
    base: str,
    ip: str,
    clock: Clock,
    rng: random.Random,
    *,
    mask_ua: bool = True,
    phone: bool = False,
) -> Any:
    w, h = rng.choice(PHONES if phone else VIEWPORTS)
    extra: dict[str, Any] = {"user_agent": _desktop_ua(browser)} if mask_ua else {}
    if phone:
        extra = {
            "user_agent": _phone_ua(browser),
            "has_touch": True,
            "is_mobile": True,
            "device_scale_factor": 3,
        }
    return await browser.new_context(
        **extra,
        base_url=base,
        ignore_https_errors=True,  # self-signed lab certificate
        viewport={"width": w, "height": h},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        extra_http_headers={"X-Lab-Client-IP": ip, "X-Lab-Time-Offset": clock.header()},
    )


def _listen(page: Any, truth: Truth, who: list[Any], sessao: str) -> None:
    """Collect the engine's assessment ids for this session (lab-only response header)."""

    def on_response(resp: Any) -> None:
        if av := resp.headers.get("x-lab-avaliacao-id"):
            truth.record(av, resp.headers.get("x-lab-decisao", ""), who[0], sessao)

    page.on("response", on_response)


async def human_session(
    browsers, base, rng, clock, truth, world, strokes, sessao, touch_share
) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    persona = pick_persona(rng, world, touch_share)
    returning = bool(world.accounts) and (persona.returning_only or rng.random() < 0.45)
    known, device = rng.choice(world.accounts) if returning else (None, [])
    ctx = await _context(
        browsers["stealth"], base, persona.ip(rng), clock, rng, phone=persona.touch
    )
    try:
        if known and not persona.new_device:
            await ctx.add_cookies(device)  # same device as when the account was created
        page = await ctx.new_page()
        cenario = "cliente_recorrente" if known else "cliente_novo"
        _listen(page, truth, [actor_for(persona, cenario)], sessao)
        before = truth.count
        created = await run_human(page, rng, persona, strokes, known)
        if created and truth.count > before:
            cookies = [c for c in await ctx.cookies() if c["name"] == "did"]
            world.accounts.append((created, cookies))
    finally:
        await ctx.close()


async def fraud_session(
    browsers, base, rng, clock, truth, world, strokes, sessao, touch_share
) -> None:  # type: ignore[no-untyped-def]
    from .fraud import SCENARIOS, actors, run_fraudster
    from .human import PERSONAS

    cenario = rng.choice(SCENARIOS)
    phone = rng.random() < (0.3 if touch_share is None else touch_share)
    persona = next(p for p in PERSONAS if p.touch == phone and p.nome in {"padrao", "celular"})
    ctx = await _context(
        browsers["stealth"], base, persona.ip(rng), clock, rng, phone=persona.touch
    )
    try:
        page = await ctx.new_page()
        who = [actors(cenario)[0]]
        _listen(page, truth, who, sessao)
        await run_fraudster(page, rng, persona, strokes, world, who, cenario)
    finally:
        await ctx.close()


async def farm_session(
    browsers, base, rng, clock, truth, world, strokes, sessao, touch_share, pool, farm
) -> None:  # type: ignore[no-untyped-def]
    from attackers.evasive import actor, farm_ip, run_farm_account

    if not pool:
        raise RuntimeError("the farm ran out of identities")
    ident = pool.pop()
    touch = rng.random() < (0.3 if touch_share is None else touch_share)
    ctx = await _context(browsers["stealth"], base, farm_ip(rng), clock, rng, phone=touch)
    try:
        page = await ctx.new_page()  # a new context is a new device: no cookies carried
        _listen(page, truth, [actor(farm)], sessao)
        await run_farm_account(page, rng, strokes, ident, touch)
    finally:
        await ctx.close()


async def script_session(
    browsers, base, rng, clock, truth, world, nivel, sessao, touch_share
) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    cenario = rng.choice(SCENARIOS)
    if nivel == 1 and rng.random() < 0.5:
        await l1_http(base, rng, clock, truth, world, cenario, sessao)
        return
    stealth = nivel == 2
    # Some level 2 scripts pose as phones; otherwise "touch" would be a free pass.
    touch = stealth and rng.random() < (0.35 if touch_share is None else touch_share)
    browser = browsers["stealth" if stealth else "plain"]
    ctx = await _context(
        browser, base, attacker_ip(rng, nivel), clock, rng, mask_ua=stealth, phone=touch
    )
    try:
        page = await ctx.new_page()
        perfil = (
            ("furtivo_toque" if touch else "navegador_furtivo") if stealth else "playwright_padrao"
        )
        _listen(page, truth, [actor(nivel, perfil, cenario, "navegador")], sessao)
        await Script(page, rng, world, stealth=stealth, touch=touch).run(cenario)
    finally:
        await ctx.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--humans", type=int, default=60)
    ap.add_argument("--l1", type=int, default=20)
    ap.add_argument("--l2", type=int, default=20)
    ap.add_argument("--fraud", type=int, default=0, help="human fraudster sessions")
    ap.add_argument("--l4", type=int, default=0, help="level 4 farm accounts (one per session)")
    ap.add_argument("--farm", default="A", help="which LLM identity pool the farm uses")
    ap.add_argument("--days", type=float, default=4.0, help="simulated time span")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument(
        "--touch",
        type=float,
        default=None,
        help="share of people and level 2 scripts forced onto phones (default: natural mix)",
    )
    ap.add_argument("--run", default=time.strftime("run-%Y%m%d-%H%M%S"))
    a = ap.parse_args()

    base = guard(SHOP_URL)
    rng = random.Random(a.seed)
    strokes = load()
    truth, world, clock = Truth(a.run), World(), Clock(a.days)

    plan = ["h"] * a.humans + ["1"] * a.l1 + ["2"] * a.l2 + ["f"] * a.fraud + ["4"] * a.l4
    pool = []
    if a.l4:
        from attackers.evasive import load_pool

        pool = load_pool(a.farm)
        random.Random(a.seed).shuffle(pool)
    rng.shuffle(plan)
    # start with a few people so attackers have accounts to aim at
    plan.sort(key=lambda k: 0 if k == "h" else 1)
    head, tail = plan[: min(a.concurrency, a.humans)], plan[min(a.concurrency, a.humans) :]
    rng.shuffle(tail)
    plan = head + tail
    batches = [plan[i : i + a.concurrency] for i in range(0, len(plan), a.concurrency)]
    jump = a.days * 86_400 / max(len(batches), 1)

    # Imported here so the module can be inspected without the browsers installed.
    from patchright.async_api import async_playwright as stealth_playwright
    from playwright.async_api import async_playwright as plain_playwright

    started, failed, n = time.monotonic(), 0, 0
    async with stealth_playwright() as sp, plain_playwright() as pp:
        browsers = {
            # full Chromium in new-headless mode: no automation flag, ordinary user agent
            "stealth": await sp.chromium.launch(channel="chromium", headless=True),
            # the default headless shell, exactly as a naive script would use it
            "plain": await pp.chromium.launch(headless=True),
        }
        for bi, batch in enumerate(batches):
            jobs = []
            for kind in batch:
                n += 1
                sessao = f"{a.run}-{n}"
                srng = random.Random(rng.random())
                if kind == "h":
                    job = human_session(
                        browsers, base, srng, clock, truth, world, strokes, sessao, a.touch
                    )
                elif kind == "4":
                    job = farm_session(
                        browsers,
                        base,
                        srng,
                        clock,
                        truth,
                        world,
                        strokes,
                        sessao,
                        a.touch,
                        pool,
                        a.farm,
                    )
                elif kind == "f":
                    job = fraud_session(
                        browsers, base, srng, clock, truth, world, strokes, sessao, a.touch
                    )
                else:
                    job = script_session(
                        browsers, base, srng, clock, truth, world, int(kind), sessao, a.touch
                    )
                # a person creating several accounts by hand takes a while
                limit = SESSION_TIMEOUT_S * (2.5 if kind in {"f", "4"} else 1)
                jobs.append(asyncio.wait_for(job, limit))
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for kind, res in zip(batch, results, strict=True):
                if isinstance(res, Exception):
                    failed += 1
                    print(f"  sessão {kind} falhou: {type(res).__name__}: {str(res)[:120]}")
            clock.jump(jump * rng.uniform(0.4, 1.6))
            done = sum(len(b) for b in batches[: bi + 1])
            print(
                f"[{time.monotonic() - started:5.0f}s] {done}/{len(plan)} sessões, "
                f"{truth.count} avaliações, {len(world.accounts)} contas, {failed} falhas",
                flush=True,
            )
        for b in browsers.values():
            await b.close()
    print(f"gabarito: {truth.path}")


if __name__ == "__main__":
    asyncio.run(main())
