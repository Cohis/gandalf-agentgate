# Gandalf — AgentGate

**Anti-agent abuse system.** A real-time risk engine that detects and stops attacks and fraud carried out by AI agents (and automation in general) against an online shop, without stopping people or the agents that act with authorisation.

> "You shall not pass." — but only to those who shouldn't.

[Versão em português](README.md)

The system's question is not "human or bot?". Legitimate agents exist: people send an assistant to buy for them. The question is:

**Who is acting, on whose behalf, and with what authorisation?**

| Actor | What the engine does |
|---|---|
| Human | Fraud analysis; when in doubt, an out-of-session confirmation |
| Declared agent (Web Bot Auth signature + the owner's mandate) | Approves inside the mandate; outside it, asks the owner |
| Disguised automation (an agent or script passing as a person) | Challenges with a passkey or denies |

The challenge is a **passkey on the account owner's device**, not a captcha: multimodal models solve captchas, but the agent does not hold the owner's authenticator.

## Final result

Collected on 13 Nov 2026 with every layer live. The models were rebuilt from scratch and the evaluation uses the most recent period of each batch and kind of actor. Decisions as the engine makes them: a challenge or a denial counts as flagged.

| Group | Sessions | Result | 95 % CI |
|---|---|---|---|
| Level 1: HTTP script and stock Playwright | 6 | 100 % caught | 100 to 100 % |
| Level 2: stealth browser | 6 | 85 % caught | 50 to 100 % |
| Level 3: AI agent (local Qwen3.5-9B, real browser) | 9 | 100 % caught | 100 to 100 % |
| Level 4: account farm, LLM-generated identities, human mouse paths | 28 | 88 % caught | 77 to 96 % |
| Human fraud (account takeover, coupon abuse, card testing) | 7 | 83 % caught | 47 to 98 % |
| Simulated people, including screen reader, voice control and phone | 55 | **2.9 % blocked** | 0 to 6.4 % |
| … with a disability | 10 | 0 % blocked | 0 to 0 % |
| **Real people (11 volunteers), none of them in training** | 47 | **0 % blocked** | 0 to 0 % |
| Level 5: legitimate agent, steps inside the mandate | 40 | 100 % approved | 100 to 100 % |
| Level 5: legitimate agent, steps outside the mandate | 13 | 100 % referred to the owner | 100 to 100 % |

Blocking by IP alone catches 19 % of the farm and 2 % of human fraud, and blocks 29 % of people on mobile (CGNAT). A farm never seen before has 79 to 90 % of its accounts caught, but only because the system saw another farm; with no farm at all in training, it drops to 10 to 16 %.

Read this before quoting the table:

- **The attack groups in the test period are small** (6 to 28 sessions) and the intervals are wide.
- **Simulated people use real mouse paths, but their rhythm and navigation are modelled.** The real people are 11 acquaintances from a single circle, and they validate the automation model only.
- **I wrote every attacker.** An attacker I did not imagine may look like none of them.

Full report, with the ablation (in Portuguese): [docs/results/fase7-final.md](docs/results/fase7-final.md). What each number does and does not measure: [docs/limitations.md](docs/limitations.md).

## What the project caught itself doing wrong

The project is worth more for the mistakes it found than for the table. Each time the model looked excellent and was learning something else:

1. **An instrumentation bug** (phase 2). 87 % of the gain came from two rhythm features because clicking a field counted as two actions, for mouse users only. The model flagged screen-reader users. The telemetry was fixed and the data collected again.
2. **The simulator's settings** (phase 2). My attackers were simply faster than my people. The five absolute-speed features left the model.
3. **Phones** (phase 3). Trained on simulated people only, the model blocked 10 % of the real volunteers, all of them on phones: a touch has no cursor. The fix was touch telemetry and real people in training.
4. **How steady the rhythm is** (phases 4 and 7). Simulated people are steadier than real ones even after calibration, and the model learned "irregular = farm". Without those features the farm is still caught, and never-seen real people blocked fall from 3.5 % to 0 %.
5. **Recordings in the fraud model** (phase 7). The volunteers came through a tunnel and did a list of tasks in minutes. To the fraud model this taught that "unknown network, many events per device" is a good customer, which is what a bot looks like. Out of that model, detected human fraud rose from 74 % to 83 %.
6. **The legitimate agent looked like credential stuffing** (phase 7). Logging in from its operator's datacenter is, feature for feature, what a stolen-password bot does. The policy changed: a verified agent is judged by its mandate, not by the models.

## Project status

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Contracts, rule engine, shop, telemetry, dashboard, compose | done |
| 2 | Attackers 1 and 2, simulator, first LightGBM, explanations | done ([result](docs/results/fase2.md)) |
| 3 | Real AI agent (local LLM), recorded real people, fraud model | done ([agent](docs/results/fase3-nivel3-agente.md), [people](docs/results/fase3-toque-e-pessoas-reais.md), [fraud](docs/results/fase3-fraude.md)) |
| 4 | Scale layer: sign-up similarity, account farm | done ([result](docs/results/fase4-fazenda.md)) |
| 5 | Signed legitimate agent (Web Bot Auth + mandate) and passkey | done |
| 6 | Network and IP reputation (local tables, public sources) | done |
| 7 | Final collection and evaluation, ablation, load | done ([result](docs/results/fase7-final.md)) |
| 8 | Module B: IEEE-CIS Fraud Detection | not started (needs a Kaggle account) |

Live models: automation `20261113-2328-0444b2` and fraud `fr-20261113-2328-3b507d`.

## Performance

i5-12450HX laptop, 8 GB RAM, everything in Docker on the same machine, load generator included; one engine process, both models in the path.

| Scenario | p50 | p99 |
|---|---|---|
| Steady 200 req/s, measured by the engine | 1.9 ms | 4.3 ms |
| Steady 200 req/s, end to end | 3.8 ms | 9.7 ms |
| Burst: 200 requests at the same instant, every second | 370 ms | 490 ms |

No errors in any scenario. The burst measures queueing, not the engine. One process tops out at about 210 req/s: ask for 400 and the queue grows. Models are swapped without restarting the engine.

## Running it

```bash
make up         # lab keys + docker compose up (engine, shops, dashboard)
```

- Shop: <https://localhost:8443> (self-signed certificate, accept the warning)
- Dashboard: <http://localhost:8501>

```bash
make test       # unit, contract and online/offline parity (no Docker)
make e2e        # end to end with the compose stack up: stack, passkey, retention
make simulate RUN=collection   # labelled traffic on a private shop (port 8444)
make labels RUN=collection     # hand the answer key to the engine
make eval                      # train both models and write the reports, no activation
make train                     # train, activate and hot-reload the engine
uv run python -m eval.final --cohort f4   # final evaluation of the whole system
scripts/final_campaign.sh      # the complete final collection (about 2.5 h)
make load       # steady 200 req/s for 30 s
make load-burst # worst case: 200 clients firing at the same instant
make retencao   # LGPD: 30-day retention (run daily)
```

The level-3 attacker uses a local LLM (llama.cpp with Qwen3.5-9B on the GPU); the final campaign starts and stops the server on its own.

## How it works

```
visitor ──HTTPS──▶ proxy (JA4) ──▶ shop ──POST /avaliar──▶ engine ──▶ approve / challenge / deny
agent ──── signed (Web Bot Auth) ──┘                         │
                                     Redis (windows, anti-replay)┤
                                  PostgreSQL (events, vectors)   ┤
             agent operator (public keys, allowlisted)           ┘
```

The engine is called by the **shop's backend**, never by the browser. For every sign-up, login, add-to-cart or payment it:

1. validates the input (Pydantic; unknown fields are rejected);
2. enriches the IP from local tables (no external call per request);
3. reads the sliding windows in Redis (10 min, 1 h, 24 h) in a single round trip;
4. computes the features, including how similar the sign-up is to those of the last 6 h;
5. applies the hard rules (obvious cases);
6. scores with two models: automation and fraud;
7. checks the agent's signature and mandate, if any;
8. decides, explains in plain sentences, persists and answers.

### Two questions, crossed

- **Is this undeclared automation?** The behaviour model (pointer, touch, typing, session shape, environment), thresholded at 1 % false positives. If yes, the challenge is a passkey.
- **Is this fraud?** The transaction model (velocity, value, scale, network), which never sees the mouse or rhythm, with cost-based thresholds and a ceiling of 5 % of honest customers challenged. If yes, the challenge is an out-of-session confirmation.

The stricter answer wins. A **verified agent** goes through neither: the signature proves which operator is acting, and the mandate (a JWS the shop signs after the owner confirmed it) says on whose behalf, with which per-purchase limit, which total limit and which actions. Inside it the agent goes through; outside, the owner is asked.

### The same feature code online and offline

- **One library** (`packages/features`): `compute(event, counts, network)` is a pure function. The engine and the offline replay call the same function.
- **The engine stores the vector it used** (`feature_snapshots`), and training reads that vector. The feature was computed before the label existed, so there is no temporal leakage.
- **A test proves it**: `tests/parity` replays 300 events offline and requires vectors identical to the stored ones.

### Defence in layers

No single signal decides. The phase-7 ablation shows how much each layer weighs.

1. **Environment**: `navigator.webdriver`, fingerprint inconsistencies, JA4.
2. **Pointer and touch**: cursor path, dead-centre clicks, touch, keyboard focus (screen-reader users move with Tab and must not look like scripts).
3. **Session**: the agent goes straight to the goal; a person wanders.
4. **Velocity and scale**: accounts per device, per IP, per card, and sign-ups written the same way (hashed n-grams; the text is never stored).
5. **Traps**: an invisible field and canary text. They are **signals for the model, never a block**, because screen readers read hidden content too; the rule loader refuses rules that use them.
6. **Network and reputation**: ASN, Spamhaus DROP, FireHOL, Tor exits and the shop's own history per /24. Just another feature: in Brazil, carrier CGNAT puts thousands of people behind one IPv4.
7. **Identity**: Web Bot Auth (RFC 9421) and the mandate.
8. **Challenge**: passkey (WebAuthn) or out-of-session confirmation.

## Security and privacy

- **Attacks only against the lab shop.** The traffic generators refuse any non-local target. Do not point anything here at third-party systems.
- **The only trusted IP is the one the proxy saw.** A client can send any `X-Forwarded-For` it likes; the shop reads only the entry the proxy appended.
- **Agent key directories only from an allowlist.** The engine never fetches keys from a URL a request names, which would be an SSRF.
- **LGPD (Brazil's GDPR)** ([docs/lgpd.md](docs/lgpd.md)): the raw IP lives only in Redis for up to 24 h; the database keeps a keyed hash and the prefix. Card numbers never reach the engine. Sign-up text is not stored. The browser sends aggregates only. Real events are dropped after 30 days, and a volunteer can be erased by their code.
- **Decision reasons are not shown to the visitor**: they would teach the attacker how to adapt.

Threat model: [docs/threat-model.md](docs/threat-model.md).

## Layout

```
packages/contracts     API contracts (Pydantic) and keys, single source of truth
packages/features      the feature library, pure
services/engine        the engine (FastAPI): pipeline, rules, policy, identity, persistence
services/shop          the lab shop and the telemetry script (TypeScript)
services/agent_operator  the lab's agent operator (key directory)
services/dashboard     dashboard (Streamlit)
simulator              simulated people and fraudsters, in a real browser
attackers              levels 1 to 5
jobs                   training, IP reputation, retention
eval                   final evaluation of the system
migrations             PostgreSQL schema
tests                  contract, unit, parity, end to end
loadtest               open-loop load and Locust
```

Code is in English; API fields are in Portuguese. Credits for datasets and libraries: [docs/credits.md](docs/credits.md).
