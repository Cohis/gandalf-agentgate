#!/usr/bin/env bash
# Phase 7: the final data campaign (feature set f4), every actor against the
# simulator's shop. Each kind of actor runs as its own batch, one after the other, and
# its answer key goes to the engine at the end of the batch. eval/final.py splits train
# and test in time WITHIN each kind of actor.
#
#   docker compose --profile sim up -d --build --wait   # engine freshly started
#   scripts/final_campaign.sh                            # ~3.5 h on the lab notebook
#
# Do not rebuild or restart the engine or shop-sim while it runs: the scale window and
# the shop's accounts live in memory.
set -uo pipefail
cd "$(dirname "$0")/.."
export LAB_SHOP_URL=https://localhost:8444
LOG=data/runs
P=f4

step() { echo "[$(date +%T)] $*"; }
labels() { uv run python -m simulator.labels "$LOG/$1.jsonl" >> "$LOG/$1.log" 2>&1; step "rótulos de $1: $(tail -1 "$LOG/$1.log")"; }

step "base: pessoas, fraude humana, níveis 1 e 2"
uv run python -m simulator.run --humans 180 --fraud 30 --l1 15 --l2 20 --days 2 \
    --concurrency 8 --seed 301 --run $P-base > $LOG/$P-base.log 2>&1
labels $P-base

step "fazenda A (nível 4) no meio de pessoas"
uv run python -m simulator.run --humans 40 --l4 70 --farm A --l1 0 --l2 0 --days 0.3 \
    --concurrency 8 --seed 302 --run $P-fazendaA > $LOG/$P-fazendaA.log 2>&1
labels $P-fazendaA

step "agente legítimo (nível 5)"
uv run python -m attackers.legit_agent --sessions 40 --fora 0.25 --days 0.5 --seed 303 \
    --run $P-nivel5 > $LOG/$P-nivel5.log 2>&1
labels $P-nivel5

step "agente de IA (nível 3): sobe o Qwen local"
LLM_EXTRA_ARGS="--reasoning off --cache-ram 0 -c 16384" ~/.local/bin/llm-server \
    > $LOG/$P-llm-server.log 2>&1 &
LLM_PID=$!
for _ in $(seq 1 90); do
    curl -sf http://127.0.0.1:8080/health > /dev/null && break
    sleep 2
done
uv run python -m attackers.agent --sessions 40 --days 1 --seed 304 \
    --run $P-nivel3 > $LOG/$P-nivel3.log 2>&1
kill $LLM_PID 2> /dev/null; wait $LLM_PID 2> /dev/null
labels $P-nivel3

step "fazenda B (nível 4) no meio de pessoas"
uv run python -m simulator.run --humans 40 --l4 70 --farm B --l1 0 --l2 0 --days 0.3 \
    --concurrency 8 --seed 305 --run $P-fazendaB > $LOG/$P-fazendaB.log 2>&1
labels $P-fazendaB

step "fim da coleta"
