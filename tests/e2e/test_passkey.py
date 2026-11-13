"""Passkey end to end, in a real browser with a virtual authenticator (phase 5).

docker compose --profile sim up -d --wait && make e2e

The owner signs up and registers a passkey. Later the same session declares itself
automated (navigator.webdriver), which the engine answers with "exigir_passkey"; the
owner confirms with the authenticator and the retried action goes through.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e

SHOP = "https://localhost:8444"  # the simulator's shop: lab mode, same code


def test_passkey_registers_and_answers_a_challenge() -> None:
    sync_api = pytest.importorskip("patchright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium", headless=True)
        ctx = browser.new_context(base_url=SHOP, ignore_https_errors=True, locale="pt-BR")
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

        page.goto("/cadastro")
        f = 'form[action="/cadastro"]'
        page.fill(f"{f} [name=nome]", "Teste Passkey")
        page.fill(f"{f} [name=email]", f"passkey-{uuid.uuid4().hex[:8]}@exemplo.test")
        page.fill(f"{f} [name=senha]", "senha-de-teste-123")
        page.click(f"{f} button[type=submit]")
        page.wait_for_load_state()
        # The test clicks like a script, so the engine rightly flags the signup; the
        # shadow shop lets it through, which is what this test needs.

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
        add = page.locator('form[action="/carrinho/adicionar"] button').first
        add.click()
        page.wait_for_load_state()
        assert "Confirme que é você" in page.content(), "webdriver session was not challenged"
        page.click("[data-passkey=confirmar]")
        status.filter(has_text="Confirmado").wait_for(timeout=10_000)

        # The verified session passes on the retry.
        page.goto("/")
        page.locator('form[action="/carrinho/adicionar"] button').first.click()
        page.wait_for_load_state()
        assert "Confirme que é você" not in page.content()
        browser.close()
