/**
 * Gandalf page telemetry.
 *
 * Privacy by construction: raw mouse coordinates and keystrokes never leave this
 * closure. Only the aggregates below are written to the hidden `telemetria` field
 * of each form, and the shop backend forwards them to the risk engine.
 *
 * Nothing here is trusted on its own: a client can forge all of it. The server
 * re-checks the traps, and the absence of this payload is itself a signal.
 */
(() => {
  type Typing = "colada" | "constante" | "humana" | "ausente";
  const KEY = "gandalf_t";
  const MAX_ACTIONS = 200;

  // Carried across page loads so a multi-page session has a rhythm. Aggregates only.
  interface Carry {
    acts: number[]; last: number; lin: number[]; clicks: number; center: number;
    downs: number; touch: number; area: number; kbd: number; prog: number;
  }
  const carry: Carry = (() => {
    try {
      const raw = sessionStorage.getItem(KEY);
      // state saved by an older script has no touch counters
      if (raw) return Object.assign({ downs: 0, touch: 0, area: 0, kbd: 0, prog: 0 }, JSON.parse(raw)) as Carry;
    } catch { /* storage blocked: start fresh */ }
    return { acts: [], last: 0, lin: [], clicks: 0, center: 0, downs: 0, touch: 0, area: 0, kbd: 0, prog: 0 };
  })();

  const save = () => {
    try { sessionStorage.setItem(KEY, JSON.stringify(carry)); } catch { /* ignore */ }
  };

  // --- rhythm: gaps between meaningful actions (click, entering a field, submit)
  const action = () => {
    const now = Date.now();
    // One gesture can fire several events (clicking a field = focusin + click).
    if (carry.last && now - carry.last < 50) return;
    if (carry.last) {
      carry.acts.push(Math.min(now - carry.last, 3_600_000));
      if (carry.acts.length > MAX_ACTIONS) carry.acts.shift();
    }
    carry.last = now;
  };

  // --- cursor: how straight is the path between two clicks (1 = ruler-straight)
  let start: [number, number] | null = null;
  let prev: [number, number] | null = null;
  let travelled = 0;
  addEventListener("mousemove", (e) => {
    const p: [number, number] = [e.clientX, e.clientY];
    if (!start) start = p;
    if (prev) travelled += Math.hypot(p[0] - prev[0], p[1] - prev[1]);
    prev = p;
  }, { passive: true });

  addEventListener("click", (e) => {
    if (start && prev && travelled > 20) {
      const straight = Math.hypot(prev[0] - start[0], prev[1] - start[1]);
      carry.lin.push(Math.min(straight / travelled, 1));
      if (carry.lin.length > 50) carry.lin.shift();
    }
    start = prev = null;
    travelled = 0;

    const el = e.target as Element | null;
    // Keyboard-activated clicks report (0,0): they say nothing about aim, skip them.
    if (el && (e.clientX || e.clientY)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        const dx = Math.abs(e.clientX - (r.left + r.width / 2)) / (r.width / 2);
        const dy = Math.abs(e.clientY - (r.top + r.height / 2)) / (r.height / 2);
        carry.clicks++;
        if (dx < 0.1 && dy < 0.1) carry.center++;
      }
    }
    action();
    publish();
  }, true);

  // --- typing: cadence only, never which keys
  const keyGaps: number[] = [];
  let lastKey = 0;
  let keys = 0;
  let pasted = false;
  let filledWithoutKeys = false;
  addEventListener("keydown", (e) => {
    if (!(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) return;
    const now = performance.now();
    if (lastKey) keyGaps.push(now - lastKey);
    if (keyGaps.length > 300) keyGaps.shift();
    lastKey = now;
    keys++;
  }, true);
  addEventListener("paste", () => { pasted = true; }, true);
  addEventListener("input", (e) => {
    const t = e.target;
    // Value changed with no keystrokes: paste, password manager, autofill or script.
    if ((t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) && t.type !== "hidden" && keys === 0) {
      filledWithoutKeys = true;
    }
  }, true);
  // Clicking a field fires pointerdown -> focusin -> click. That is ONE action: the
  // click counts it. Only focus that arrives by keyboard (Tab) is an action of its own.
  // Counting both made every mouse user look like they acted twice ~60 ms apart.
  let lastPointerDown = -Infinity;
  addEventListener("pointerdown", (e) => {
    lastPointerDown = performance.now();
    // Finger or mouse? On a phone there is no cursor path, and that must not be
    // mistaken for a script that never moved the mouse.
    carry.downs++;
    if (e.pointerType === "touch" || e.pointerType === "pen") {
      carry.touch++;
      carry.area += Math.min(Math.max(e.width || 0, e.height || 0), 500);
    }
  }, true);
  // How focus moves when it is not a click: a keyboard or screen-reader user presses
  // Tab; a script sets focus in code. Without this, navigating without a mouse looks
  // exactly like a bot that never moved one.
  let lastTab = -Infinity;
  addEventListener("keydown", (e) => { if (e.key === "Tab") lastTab = performance.now(); }, true);
  addEventListener("focusin", () => {
    const now = performance.now();
    if (now - lastPointerDown <= 1000) return;
    if (now - lastTab < 500) carry.kbd++; else carry.prog++;
  }, true);
  addEventListener("focusin", (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      lastKey = 0; // a pause between fields is not typing cadence
      if (performance.now() - lastPointerDown > 1000) action();
      publish();
    }
  }, true);

  const typing = (): Typing => {
    if (keyGaps.length >= 5) {
      const mean = keyGaps.reduce((a, b) => a + b, 0) / keyGaps.length;
      const sd = Math.sqrt(keyGaps.reduce((a, b) => a + (b - mean) ** 2, 0) / keyGaps.length);
      return mean > 0 && sd / mean < 0.12 ? "constante" : "humana";
    }
    if (pasted || filledWithoutKeys) return "colada";
    return keys > 0 ? "humana" : "ausente";
  };

  // --- environment: count contradictions, do not build an identifier
  const inconsistencies = (): number => {
    const ua = navigator.userAgent;
    const nav = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
    let n = 0;
    const mobileUa = /Android|iPhone|Mobile/.test(ua);
    if (mobileUa && navigator.maxTouchPoints === 0) n++;
    if (nav.userAgentData && nav.userAgentData.mobile !== undefined && nav.userAgentData.mobile !== mobileUa) n++;
    const plat = navigator.platform || "";
    if (/Windows/.test(ua) && !/^Win/.test(plat)) n++;
    if (/Macintosh/.test(ua) && !/^Mac/.test(plat)) n++;
    if (/Linux|X11/.test(ua) && !/Android/.test(ua) && !/Linux/.test(plat)) n++;
    if (!navigator.languages || navigator.languages.length === 0) n++;
    if (/Chrome\//.test(ua) && !(window as unknown as { chrome?: unknown }).chrome) n++;
    if (window.outerWidth === 0 || window.outerHeight === 0) n++;
    if (screen.width < window.innerWidth || screen.height < window.innerHeight) n++;
    try {
      const gl = document.createElement("canvas").getContext("webgl");
      const ext = gl?.getExtension("WEBGL_debug_renderer_info");
      const renderer = gl && ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "";
      if (/SwiftShader|llvmpipe|Software/i.test(renderer)) n++;
    } catch { /* WebGL unavailable is not a contradiction */ }
    return n;
  };
  const fpIssues = inconsistencies();

  const mean = (xs: number[]): number | null =>
    xs.length ? Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 1000) / 1000 : null;

  const snapshot = () => ({
    coletada: true,
    intervalos_acao_ms: carry.acts.slice(-MAX_ACTIONS),
    trajetoria_cursor_linear: mean(carry.lin),
    cliques_no_centro: carry.clicks ? Math.round((carry.center / carry.clicks) * 1000) / 1000 : null,
    toque: carry.downs ? Math.round((carry.touch / carry.downs) * 1000) / 1000 : null,
    area_toque: carry.touch ? Math.round((carry.area / carry.touch) * 10) / 10 : null,
    foco_teclado: carry.kbd + carry.prog ? Math.round((carry.kbd / (carry.kbd + carry.prog)) * 1000) / 1000 : null,
    digitacao: typing(),
    webdriver: navigator.webdriver === true,
    inconsistencias_fingerprint: fpIssues,
    // Traps are decided by the server from the submitted form, never by this script.
    honeypot_preenchido: false,
    canario_detectado: false,
  });

  // Kept fresh on every action, so it is there even if a form is submitted
  // programmatically (form.submit() fires no submit event).
  function publish(): void {
    save();
    const value = JSON.stringify(snapshot());
    document.querySelectorAll<HTMLInputElement>('input[name="telemetria"]').forEach((i) => { i.value = value; });
  }

  addEventListener("submit", () => { action(); publish(); }, true);
  addEventListener("pagehide", save);
  publish();
})();
