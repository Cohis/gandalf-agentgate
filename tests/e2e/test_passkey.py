"""Passkey end to end, in a real browser with a virtual authenticator (phase 5).

docker compose --profile sim up -d --wait && make e2e

The owner signs up and registers a passkey. Later the same session declares itself
automated (navigator.webdriver), which the engine answers with "exigir_passkey"; the
owner confirms with the authenticator and the retried action goes through.
"""

from __future__ import annotations

import random
import uuid

import pytest

pytestmark = pytest.mark.e2e

SHOP = "https://localhost:8444"  # the simulator's shop: lab mode, same code
# The owner's own browser, not a headless bot: an ordinary desktop user-agent.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class Person:
    """Drives the page roughly like a person: the pointer travels to a point near (not
    at) the middle of the target, and text is typed key by key. The engine denies a
    session that clicks like a script outright (score above 0.99), and this test is
    about what happens AFTER the owner declares automation, not about that."""

    def __init__(self, page) -> None:  # type: ignore[no-untyped-def]
        self.page, self.rng, self.pos = page, random.Random(7), (300.0, 200.0)

    def click(self, selector: str) -> None:
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        x = box["x"] + box["width"] * self.rng.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * self.rng.uniform(0.3, 0.7)
        self.page.mouse.move(*self.pos)
        self.page.mouse.move(x, y, steps=self.rng.randint(12, 25))
        self.page.wait_for_timeout(self.rng.randint(150, 400))
        self.page.mouse.click(x, y)
        self.pos = (x, y)

    def type(self, selector: str, text: str) -> None:
        self.click(selector)
        self.page.keyboard.type(text, delay=self.rng.randint(90, 160))
        self.page.wait_for_timeout(self.rng.randint(300, 900))


def test_passkey_registers_and_answers_a_challenge() -> None:
    sync_api = pytest.importorskip("patchright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium", headless=True)
        ctx = browser.new_context(
            base_url=SHOP, ignore_https_errors=True, locale="pt-BR", user_agent=UA
        )
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        auth = cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                }
            },
        )

        person = Person(page)
        page.goto("/cadastro")
        page.wait_for_timeout(1200)
        f = 'form[action="/cadastro"]'
        person.type(f"{f} [name=nome]", "Teste Passkey")
        person.type(f"{f} [name=email]", f"passkey-{uuid.uuid4().hex[:8]}@exemplo.test")
        person.type(f"{f} [name=senha]", "senha-de-teste-123")
        with page.expect_navigation():
            person.click(f"{f} button[type=submit]")

        page.goto("/conta/passkey")
        page.click("[data-passkey=registrar]")
        status = page.locator("#passkey-status")
        status.filter(has_text="Passkey criada").wait_for(timeout=10_000)
        creds = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth["authenticatorId"]})
        assert len(creds["credentials"]) == 1

        # From now on the browser says it is driven by automation, and the shadow shop
        # enforces the verdicts for this browser (lab-only header).
        ctx.set_extra_http_headers({"X-Lab-Enforce": "1"})
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => true})")
        page.goto("/")
        page.wait_for_timeout(1500)
        with page.expect_navigation():
            person.click('form[action="/carrinho/adicionar"] button')
        assert "Confirme que é você" in page.content(), "webdriver session was not challenged"
        page.click("[data-passkey=confirmar]")
        status.filter(has_text="Confirmado").wait_for(timeout=10_000)

        # The verified session passes on the retry.
        page.goto("/")
        page.wait_for_timeout(1500)
        with page.expect_navigation():
            person.click('form[action="/carrinho/adicionar"] button')
        assert "Confirme que é você" not in page.content()
        browser.close()
