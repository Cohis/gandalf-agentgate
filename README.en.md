# Gandalf — AgentGate

**Anti-agent abuse system.** A real-time risk engine that detects and stops attacks and fraud carried out by AI agents (and automation in general) against an online shop.

> "You shall not pass." — but only for those who should not.

[Versão em português](README.md)

The question is not "human or bot?". Legitimate agents exist: people send an assistant to shop for them. The question is:

**Who is acting, on whose behalf, and with which authorization?**

| Actor | What the engine does |
|---|---|
| Human | Regular fraud analysis |
| Declared agent (signed, carrying a user mandate) | Approves within the mandate; outside it, asks the account owner |
| Disguised automation (agent or script posing as a human) | Challenges or denies |

The challenge is a **passkey on the owner's device**, not a captcha: multimodal models already solve captchas, but the agent does not hold the account owner's authenticator.

## Project status

Work in progress, built in phases. This table states what actually exists today.

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Skeleton: contracts, rule-based engine, shop, telemetry, dashboard, compose | **done and validated** |
| 2 | Attackers 1 and 2, simulator, first LightGBM model, SHAP | **done** ([results, in Portuguese](docs/results/fase2.md)) |
| 3 | Real AI agent (local LLM), recorded human sessions | to do |
| 4 | Scale layer: embeddings, FAISS, HDBSCAN | to do |
| 5 | Signed legitimate agent (Web Bot Auth) and passkey | to do |
| 6 | IP reputation | to do |
| 7 | Final table, ablation, load test | to do |
| 8 | Module B: IEEE-CIS Fraud Detection | to do |

### Phase 2 result, and why it is worth little

Test period (most recent), 121 sessions, threshold chosen on validation for 1% false positives:

| Group | Sessions | LightGBM model | Hand-written heuristic |
|---|---|---|---|
| Attacker level 1 (HTTP script, stock Playwright) | 18 | 100% detected | 100% |
| Attacker level 2 (stealth browser, scripted behaviour) | 32 | 100% detected | 51% |
| Simulated humans (false positives) | 71 | 0% | 2.5% |

Read this before being impressed:

- **Level 2 is easy by construction.** It hides its environment but never moves the mouse and clicks the exact centre of everything. That is what the model learned: 86% of its gain comes from `cursor_linear` and `cliques_centro`. An attacker replaying mouse paths breaks this result. That attacker is level 4.
- **The model does not generalise.** Trained with no level 2 examples and tested only on level 2, it detects **0%**.
- **Small sample.** Screen-reader users, who also use no mouse and are the likeliest false positive, appear in only 4 test sessions.
- **Humans are simulated.** Real mouse paths, modelled rhythm and typing. See [docs/limitations.md](docs/limitations.md) (Portuguese).

Two traps the project caught on the way, worth more than the table:

1. **The first model learned an instrumentation bug.** 87% of its gain sat on two rhythm features: clicking a field counted as two actions 60 ms apart, for mouse users only, so the model flagged screen-reader users. Telemetry was fixed and the data collected again.
2. **The second model learned the simulator's settings.** 73% of the gain moved to the mean gap between actions: my attackers were simply faster than my people. The five absolute-pace features were removed from the model; the ablation is in the report.

The model adds about 0.5 ms per request and is swapped without restarting the engine (5 reloads during 200 req/s, no request lost).

### Phase 1 measurements

i5-12450HX notebook, 8 GB RAM, everything in Docker on the same machine, single engine worker.

| Scenario | p50 | p99 |
|---|---|---|
| Steady 200 req/s, measured by the engine | 0.5 ms | 1.3 ms |
| Steady 200 req/s, end to end (HTTP client) | 2.4 ms | 4.6 ms |
| Burst: 200 requests in the same instant, every second (client) | 160 ms | 240 ms |

The burst is the worst case for a single worker: all 200 requests queue at once and the engine takes about 0.9 ms to serve each. There is no model or embedding in the path yet; these numbers will grow in phases 2 and 4.

## Run it

```bash
docker compose up -d --build --wait
```

- Shop: <https://localhost:8443> (self-signed certificate)
- Dashboard: <http://localhost:8501>

Without Docker (in-memory state, no JA4):

```bash
make dev        # shop on http://127.0.0.1:8080
```

Tests (no Docker needed):

```bash
make test       # unit, contract and online/offline parity
make e2e        # end to end, with the stack running
make load       # steady 200 req/s for 30 s against the engine
make load-burst # worst case: 200 clients firing in the same instant
```

## How it works

```
visitor ──HTTPS──▶ proxy (computes JA4) ──▶ shop ──POST /avaliar──▶ engine
                                                                    │
                                                  Redis (windows) ──┤
                                               PostgreSQL (events) ─┘
```

The engine is called by the **shop backend**, never by the browser. For each sign-up, login, cart addition or payment it validates the input, enriches the IP from local data (no external call per request), reads the sliding windows from Redis in one round trip, computes features, applies hard rules, scores, decides, explains the decision in plain sentences, writes the windows, queues persistence and answers.

### One feature code path, online and offline

- **One library** (`packages/features`): `compute(event, counts, network)` is a pure function called by both the engine and the offline replay. Only the store differs (Redis or memory), with identical semantics.
- **The engine logs the vector it used** (`feature_snapshots`); training reads it. The feature was computed before the label existed, so there is no temporal leakage.
- **A test proves it**: `tests/parity` replays 300 events offline and requires vectors identical to the logged ones.

### Defence in depth

No single signal decides.

1. **Environment**: `navigator.webdriver`, fingerprint inconsistencies, JA4, network type.
2. **Rhythm**: the shape of the gaps between actions, using scale-free statistics.
3. **Session**: agents go straight to the goal; humans wander.
4. **Scale**: accounts per device, per IP, per card. (Sign-up similarity arrives in phase 4.)
5. **Traps**: an invisible field and a canary text. They are **signals for the model, never a block**, because screen readers also read hidden content. The rule loader refuses any rule built on them.
6. **Challenge**: passkey (phase 5).

IP reputation is just another feature: carrier-grade NAT puts thousands of people behind one IPv4, and residential proxies make rotating IPs cheap.

## Security and privacy

- **Attacks target the lab shop only.** Traffic generators refuse any non-local target.
- **The only trusted IP is the one the proxy saw.** The proxy appends the real address to `X-Forwarded-For`; the shop reads only that last entry.
- **Data minimisation (LGPD, Brazil's data protection law)**: the raw IP lives only in Redis for up to 24 h; the database keeps a keyed hash and the prefix. Card numbers never reach the engine. Sign-up free text is not stored. The browser sends aggregates only.
- **Decision reasons are never shown to the visitor.**

## Known limitations

- The shop keeps accounts and sessions in memory: it is a lab target.
- With the current proxy, if JA4 computation fails, a client-forged `X-JA4-Fingerprint` header may be passed through. JA4 is a signal, never proof.
- In `LAB_MODE` traffic generators supply a simulated client IP and clock. Lab IPs are fictional; JA4 is real.
- Browser telemetry can be forged by anyone who reads the script. The defence never relies on it alone.

Code is in English; API field names are in Portuguese.
