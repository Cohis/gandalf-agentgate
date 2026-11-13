// Passkeys (WebAuthn) for the lab shop: register one, or answer a challenge with it.
(() => {
  const b64u = {
    dec: (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0)),
    enc: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  };
  const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body, credentials: "same-origin" });
  const say = (el, text, ok) => { el.textContent = text; el.className = ok ? "frete-gratis" : "erro"; };

  async function register(btn) {
    const out = document.getElementById("passkey-status");
    const opts = await (await post("/conta/passkey/opcoes")).json();
    opts.challenge = b64u.dec(opts.challenge);
    opts.user.id = b64u.dec(opts.user.id);
    (opts.excludeCredentials || []).forEach((c) => { c.id = b64u.dec(c.id); });
    const cred = await navigator.credentials.create({ publicKey: opts });
    const r = await post("/conta/passkey/registrar", JSON.stringify({
      id: cred.id, rawId: b64u.enc(cred.rawId), type: cred.type,
      response: { clientDataJSON: b64u.enc(cred.response.clientDataJSON), attestationObject: b64u.enc(cred.response.attestationObject),
                  transports: cred.response.getTransports ? cred.response.getTransports() : [] },
    }));
    say(out, r.ok ? "Passkey criada. A partir de agora ela confirma que é você." : "Não foi possível criar a passkey.", r.ok);
    if (r.ok) btn.disabled = true;
  }

  async function confirm(btn) {
    const out = document.getElementById("passkey-status");
    const id = btn.dataset.desafio;
    const opts = await (await post(`/desafio/${id}/opcoes`)).json();
    opts.challenge = b64u.dec(opts.challenge);
    (opts.allowCredentials || []).forEach((c) => { c.id = b64u.dec(c.id); });
    const cred = await navigator.credentials.get({ publicKey: opts });
    const r = await post(`/desafio/${id}/confirmar`, JSON.stringify({
      id: cred.id, rawId: b64u.enc(cred.rawId), type: cred.type,
      response: { clientDataJSON: b64u.enc(cred.response.clientDataJSON), authenticatorData: b64u.enc(cred.response.authenticatorData),
                  signature: b64u.enc(cred.response.signature), userHandle: cred.response.userHandle ? b64u.enc(cred.response.userHandle) : null },
    }));
    say(out, r.ok ? "Confirmado. Volte e refaça a ação." : "A passkey não confere.", r.ok);
    if (r.ok) btn.disabled = true;
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-passkey]");
    if (!btn) return;
    e.preventDefault();
    const run = btn.dataset.passkey === "registrar" ? register : confirm;
    run(btn).catch(() => say(document.getElementById("passkey-status"), "Operação cancelada ou não suportada neste navegador.", false));
  });
})();
