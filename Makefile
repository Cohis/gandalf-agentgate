.PHONY: simulate labels train reload help up down logs test lint e2e load load-burst telemetry dev psql clean

RUN ?= coleta

help:            ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

up:              ## build and start the whole lab
	docker compose up -d --build --wait
	@echo "loja:   https://localhost:8443  (certificado autoassinado)"
	@echo "painel: http://localhost:8501"

down:            ## stop the lab (keeps data)
	docker compose down

clean:           ## stop the lab and delete its data
	docker compose down -v

logs:            ## follow service logs
	docker compose logs -f --tail=50

test:            ## unit, contract and parity tests (no Docker needed)
	uv run pytest -q

lint:            ## ruff
	uv run ruff check . && uv run ruff format --check .

e2e:             ## end-to-end tests against the running lab
	uv run --with "psycopg[binary]" pytest -q -m e2e tests/e2e

load:            ## steady 200 req/s for 30 s; prints the engine's p50/p99
	uv run python loadtest/open_loop.py --rps 200 --seconds 30

load-burst:      ## worst case: 200 clients firing in the same instant, every second
	uv run locust -f loadtest/locustfile.py --headless -H http://127.0.0.1:8000 \
		-u 200 -r 200 -t 30s --only-summary

simulate:        ## labelled lab traffic: people + attackers 1 and 2 (shop in shadow mode)
	SHOP_SHADOW=1 docker compose up -d --wait shop proxy
	uv run python -m simulator.run --humans 300 --l1 110 --l2 150 --days 6 --concurrency 12 --run $(RUN)

labels:          ## deliver a run's answer key to the engine: make labels RUN=<name>
	uv run python -m simulator.labels data/runs/$(RUN).jsonl

train:           ## train + evaluate, write eval/out/fase2.md, activate and hot-reload
	uv run python -m jobs.train --activate
	$(MAKE) reload

reload:          ## tell the running engine to load models/ACTIVE
	curl -fsS -X POST -H "X-Gandalf-Key: $${GANDALF_SERVICE_KEY:-dev-only-change-me}" \
		http://127.0.0.1:8000/admin/recarregar-modelo && echo

telemetry:       ## build the page script for local development
	npx --yes esbuild@0.25.0 services/shop/telemetry/telemetry.ts --bundle --minify \
		--target=es2020 --outfile=services/shop/src/gandalf_shop/static/telemetry.js

dev: telemetry   ## engine + shop in one process, no Docker (in-memory state)
	uv run python scripts/dev_local.py

psql:            ## open a SQL shell on the lab database
	docker compose exec postgres psql -U gandalf gandalf
