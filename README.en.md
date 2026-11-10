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
| 1 | Skeleton: contracts, rule-based engine, shop, telemetry, dashboard, compose | **code done; Docker run still to be validated** |
| 2 | Attackers 1 and 2, simulator, first LightGBM model, SHAP | to do |
| 3 | Real AI agent (local LLM), recorded human sessions | to do |
| 4 | Scale layer: embeddings, FAISS, HDBSCAN | to do |
| 5 | Signed legitimate agent (Web Bot Auth) and passkey | to do |
| 6 | IP reputation | to do |
| 7 | Final table, ablation, load test | to do |
| 8 | Module B: IEEE-CIS Fraud Detection | to do |

In phase 1 the score comes from a **hand-written heuristic** (`heuristica-0`), not a trained model. It exists so the pipeline works end to end and as the baseline the model will have to beat. There are no detection numbers to show yet.

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
make load       # 50 req/s for 60 s against the engine
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
