.PHONY: help up down logs test lint e2e load telemetry dev psql clean

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

load:            ## 50 RPS for 60 s against the engine; prints p50/p99
	uv run locust -f loadtest/locustfile.py --headless -H http://127.0.0.1:8000 \
		-u 50 -r 50 -t 60s --only-summary

telemetry:       ## build the page script for local development
	npx --yes esbuild@0.25.0 services/shop/telemetry/telemetry.ts --bundle --minify \
		--target=es2020 --outfile=services/shop/src/gandalf_shop/static/telemetry.js

dev: telemetry   ## engine + shop in one process, no Docker (in-memory state)
	uv run python scripts/dev_local.py

psql:            ## open a SQL shell on the lab database
	docker compose exec postgres psql -U gandalf gandalf
