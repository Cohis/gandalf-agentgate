.PHONY: chaves eval public tunnel record record-status simulate labels train reload help up down logs test lint e2e load load-burst telemetry dev psql clean

RUN ?= coleta

help:            ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

up: chaves       ## build and start the whole lab
	docker compose --profile sim up -d --build --wait
	@echo "loja:   https://localhost:8443  (certificado autoassinado)"
	@echo "loja do simulador: https://localhost:8444  (tráfego rotulado, modo sombra)"
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
	uv run locust -f loadtest/locustfile.py --headless -H http://127.0.0.1:8010 \
		-u 200 -r 200 -t 30s --only-summary

simulate:        ## labelled lab traffic on a private shop (port 8444); the public shop is untouched
	docker compose --profile sim up -d --build --wait shop-sim proxy-sim
	LAB_SHOP_URL=https://localhost:8444 uv run python -m simulator.run \
		--humans 360 --l1 100 --l2 150 --fraud 110 --days 6 --concurrency 8 --run $(RUN)

labels:          ## deliver a run's answer key to the engine: make labels RUN=<name>
	uv run python -m simulator.labels data/runs/$(RUN).jsonl

train:           ## train both models, write eval/out/*.md, activate them and hot-reload
	uv run python -m jobs.train --activate
	uv run python -m jobs.train_fraud --activate
	$(MAKE) reload

eval:            ## train both models and write the reports, WITHOUT activating anything
	uv run python -m jobs.train
	uv run python -m jobs.train_fraud
	@echo "relatórios: eval/out/fase2.md (automação) e eval/out/fraude.md (fraude)"

reload:          ## tell the running engine to load models/ACTIVE
	curl -fsS -X POST -H "X-Gandalf-Key: $${GANDALF_SERVICE_KEY:-dev-only-change-me}" \
		http://127.0.0.1:8010/admin/recarregar-modelo && echo

record:          ## open the lab for recording real people at https://localhost:8443/gravar
	SHOP_RECORDING=1 docker compose up -d --build --wait
	@echo "Abra https://localhost:8443/gravar em uma janela anônima"

public:          ## recording for people on their own computers: make public CONVITE=<code>, then make tunnel
	@test -n "$(CONVITE)" || { echo "use: make public CONVITE=<codigo>"; exit 1; }
	SHOP_RECORDING=1 SHOP_LAB_MODE=0 SHOP_TUNNEL=1 SHOP_INVITE_CODE=$(CONVITE) \
		docker compose up -d --build --wait
	@echo "agora rode: make tunnel"

tunnel:          ## expose the shop (and only the shop) through ngrok
	ngrok http https://localhost:8443

record-status:   ## how many recorded sessions and events each participant has
	@docker compose exec -T postgres psql -U gandalf gandalf -c "SELECT l.perfil AS participante, \
		count(DISTINCT e.sessao_id) AS sessoes, count(*) AS eventos, \
		count(*) FILTER (WHERE d.decisao <> 'aprovar') AS seriam_barrados \
		FROM labels l JOIN events e USING (avaliacao_id) JOIN decisions d USING (avaliacao_id) \
		WHERE l.fonte = 'gravacao:humano_real' GROUP BY 1 ORDER BY 1"

telemetry:       ## build the page script for local development
	npx --yes esbuild@0.25.0 services/shop/telemetry/telemetry.ts --bundle --minify \
		--target=es2020 --outfile=services/shop/src/gandalf_shop/static/telemetry.js

dev: telemetry   ## engine + shop in one process, no Docker (in-memory state)
	uv run python scripts/dev_local.py

psql:            ## open a SQL shell on the lab database
	docker compose exec postgres psql -U gandalf gandalf

retencao:        ## LGPD: delete unlabelled events past 30 days (run daily); PARTICIPANTE=<code> erases a volunteer
	uv run python -m jobs.retention $(if $(PARTICIPANTE),--participante $(PARTICIPANTE),)

chaves:          ## the shops' mandate signing keys (lab: readable by the container user)
	@mkdir -p data/loja
	@for f in mandato-loja mandato-loja-sim; do [ -f data/loja/$$f.b64u ] || uv run python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; from cryptography.hazmat.primitives.serialization import Encoding as E, PrivateFormat as P, NoEncryption as N; from gandalf_contracts.keys import b64u; print(b64u(K.generate().private_bytes(E.Raw, P.Raw, N())))" > data/loja/$$f.b64u; chmod 644 data/loja/$$f.b64u; done
	@ls data/loja
