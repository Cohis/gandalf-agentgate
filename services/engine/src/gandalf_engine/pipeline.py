"""The online path of POST /avaliar. Every stage is timed.

validate -> (agent identity) -> enrich IP -> read windows -> features
         -> hard rules -> score -> policy -> explain
         -> write windows -> enqueue persistence -> respond
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

from gandalf_contracts import AssessRequest, AssessResponse, Decisao, Evento
from gandalf_features import (
    FEATURE_VERSION,
    ScaleWindow,
    compute,
    signup_scale_inputs,
    state_queries,
    state_writes,
)
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .enrich import Enricher, ip_hmac, ip_prefix, ua_family
from .explain import reasons
from .policy import Policy
from .rules import Rule, evaluate
from .scoring import Scorer
from .state import RedisStateStore
from .storage import Record, Storage

log = logging.getLogger("gandalf.pipeline")

_BUCKETS_MS = (1, 2, 5, 10, 20, 50, 100, 250, 1000)


@dataclass(slots=True)
class Metrics:
    decisions: dict[str, int] = field(default_factory=dict)
    degraded: int = 0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(_BUCKETS_MS) + 1))
    latency_sum_ms: float = 0.0
    count: int = 0

    def observe(self, decisao: str, latency_ms: float, degraded: bool) -> None:
        self.decisions[decisao] = self.decisions.get(decisao, 0) + 1
        self.degraded += degraded
        self.count += 1
        self.latency_sum_ms += latency_ms
        for i, edge in enumerate(_BUCKETS_MS):
            if latency_ms <= edge:
                self.buckets[i] += 1
                return
        self.buckets[-1] += 1

    def render(self, dropped: int) -> str:
        lines = ["# TYPE gandalf_decisions_total counter"]
        lines += [
            f'gandalf_decisions_total{{decisao="{d}"}} {n}' for d, n in self.decisions.items()
        ]
        lines += ["# TYPE gandalf_latency_ms histogram"]
        acc = 0
        for edge, n in zip(_BUCKETS_MS, self.buckets, strict=False):
            acc += n
            lines.append(f'gandalf_latency_ms_bucket{{le="{edge}"}} {acc}')
        lines.append(f'gandalf_latency_ms_bucket{{le="+Inf"}} {self.count}')
        lines.append(f"gandalf_latency_ms_sum {self.latency_sum_ms:.3f}")
        lines.append(f"gandalf_latency_ms_count {self.count}")
        lines.append(f"gandalf_degraded_total {self.degraded}")
        lines.append(f"gandalf_storage_dropped_total {dropped}")
        return "\n".join(lines) + "\n"


class Engine:
    def __init__(
        self,
        *,
        redis: Redis,
        storage: Storage,
        enricher: Enricher,
        rules: list[Rule],
        scorer: Scorer,
        fraud_scorer: Scorer | None = None,
        policy: Policy,
        ip_hmac_key: str,
    ) -> None:
        self._redis = redis
        self._state = RedisStateStore(redis)
        self._storage = storage
        self._enricher = enricher
        self._rules = rules
        self.scorer = scorer  # swapped atomically on model reload (phase 2)
        self.fraud_scorer = fraud_scorer
        # In-process, like the planned FAISS index: lost on restart (documented).
        self.scale = ScaleWindow()
        self._policy = policy
        self._ip_hmac_key = ip_hmac_key
        self.metrics = Metrics()

    async def assess(self, req: AssessRequest) -> AssessResponse:
        t0 = time.perf_counter()
        stages: dict[str, float] = {}
        mark = t0

        def lap(name: str) -> None:
            nonlocal mark
            now = time.perf_counter()
            stages[name] = round((now - mark) * 1000, 3)
            mark = now

        net = self._enricher.lookup(req.ip)
        lap("enriquecimento")

        now = req.momento.timestamp()  # event time: deterministic for the offline replay
        idem_key = f"idem:{req.id_requisicao}"
        degraded = False
        counts: dict[str, int] = {}
        try:
            cached, counts = await self._state.read(state_queries(req), now, idem_key)
            if cached:
                return AssessResponse.model_validate_json(cached)
        except RedisError:
            degraded = True
        lap("janelas")

        vec, shape = signup_scale_inputs(req)
        signup = req.evento is Evento.cadastro
        scale = self.scale.query(vec, shape, now) if signup else None
        features = compute(req, counts, net, scale)
        lap("features")

        fired = evaluate(self._rules, features)
        lap("regras")

        scorer, fraud_scorer = self.scorer, self.fraud_scorer
        score, contributions = scorer.score(features)
        score_fraude: float | None = None
        fraud_contrib: dict[str, float] = {}
        if fraud_scorer is not None:
            score_fraude, fraud_contrib = fraud_scorer.score(features)
        t_fraude = fraud_scorer.thresholds if fraud_scorer else None
        lap("modelo")

        decisao, acao = self._policy.decide(
            score, fired, scorer.thresholds, score_fraude=score_fraude, t_fraude=t_fraude
        )
        ator = self._policy.actor(score, scorer.thresholds)
        motivos = [r.motivo for r in fired]
        if decisao is not Decisao.aprovar:
            explain = []
            if self._policy.automation_flag(score, scorer.thresholds) is not Decisao.aprovar:
                explain += reasons(features, contributions)
            if self._policy.fraud_flag(score_fraude, t_fraude) is not Decisao.aprovar:
                explain += reasons(features, fraud_contrib)
            if not explain:  # a hard rule fired on its own
                explain = reasons(features, contributions)
            motivos += [m for m in explain if m not in motivos]
        if degraded:
            motivos.append("modo degradado: contadores de velocidade indisponíveis")
        lap("decisao")

        avaliacao_id = "av_" + secrets.token_hex(8)
        desafio_id = "ds_" + secrets.token_hex(8) if decisao is Decisao.desafiar else None

        latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        resp = AssessResponse(
            avaliacao_id=avaliacao_id,
            decisao=decisao,
            acao=acao,
            ator=ator,
            score_automacao=score,
            score_fraude=score_fraude,
            motivos=motivos,
            regras_acionadas=[r.id for r in fired],
            desafio_id=desafio_id,
            versao_modelo=scorer.version + (f"+{fraud_scorer.version}" if fraud_scorer else ""),
            versao_features=FEATURE_VERSION,
            versao_politica=self._policy.versao,
            modo_degradado=degraded,
            latencia_ms=latency_ms,
        )

        if not degraded:
            try:
                await self._state.write(
                    state_writes(req, avaliacao_id), now, idem_key, resp.model_dump_json()
                )
            except RedisError:
                log.warning("state write failed for %s", avaliacao_id)
        if signup:
            self.scale.add(vec, shape, now)
        lap("gravacao_janelas")

        record = self._record(req, resp, features, net, stages)
        if vec is not None:
            record.embedding = [round(float(x), 5) for x in vec]
        self._storage.enqueue(record)
        self.metrics.observe(decisao.value, latency_ms, degraded)
        return resp

    def _record(self, req, resp, features, net, stages) -> Record:  # type: ignore[no-untyped-def]
        event = {
            "avaliacao_id": resp.avaliacao_id,
            "id_requisicao": req.id_requisicao,
            "momento": req.momento,
            "evento": req.evento.value,
            "conta_id": req.conta_id,
            "dispositivo_id": req.dispositivo_id,
            "sessao_id": req.sessao_id,
            "ip_hmac": ip_hmac(req.ip, self._ip_hmac_key),
            "ip_prefixo": ip_prefix(req.ip),
            "asn": net.asn,
            "pais": net.pais,
            "tipo_rede": net.tipo_rede,
            "ja4": req.ja4,
            "http2_fp": req.http2_fingerprint,
            "ua_familia": ua_family(req.user_agent),
            "cartao_hash": req.cartao_hash,
            "valor": req.valor,
            "telemetria": req.telemetria.model_dump(mode="json"),
            "sessao": req.sessao.model_dump(mode="json"),
            "ator_declarado": req.assinatura_agente is not None,
        }
        decision = {
            "avaliacao_id": resp.avaliacao_id,
            "decisao": resp.decisao.value,
            "acao": resp.acao.value,
            "ator_tipo": resp.ator.tipo.value,
            "ator_confianca": resp.ator.confianca,
            "score_automacao": resp.score_automacao,
            "score_fraude": resp.score_fraude,
            "motivos": resp.motivos,
            "regras_acionadas": resp.regras_acionadas,
            "desafio_id": resp.desafio_id,
            "versao_modelo": resp.versao_modelo,
            "versao_features": resp.versao_features,
            "versao_politica": resp.versao_politica,
            "modo_degradado": resp.modo_degradado,
            "latencia_ms": resp.latencia_ms,
            "estagios_ms": stages,
        }
        snapshot = {
            "avaliacao_id": resp.avaliacao_id,
            "versao_features": FEATURE_VERSION,
            "features": features,
        }
        return Record(event=event, decision=decision, features=snapshot)
