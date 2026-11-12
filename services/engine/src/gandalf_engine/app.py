from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from gandalf_contracts import AssessRequest, AssessResponse, LabelBatch
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis

from .config import Settings
from .enrich import Enricher
from .pipeline import Engine
from .policy import Policy
from .rules import load_rules
from .scoring import HeuristicScorer, ModelError, Scorer, load_scorer
from .storage import NullStorage, PostgresStorage, Storage

log = logging.getLogger("gandalf.engine")


def _scorer_or_baseline(settings: Settings) -> Scorer:
    """At start-up a model that cannot be used (wrong feature set, missing file) is
    a warning, not an outage: the engine serves the hand-written baseline instead."""
    try:
        return load_scorer(settings.model_dir)
    except (ModelError, OSError, KeyError, ValueError) as exc:
        log.warning("active model refused, serving the baseline: %s", exc)
        return HeuristicScorer()


class ChallengeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: Literal["aprovado", "falhou", "expirado"]


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        r = redis or Redis.from_url(
            settings.redis_url, socket_timeout=0.15, socket_connect_timeout=0.3
        )
        # Open the connection now: the first customer should not pay for the handshake
        # (and be answered in degraded mode because of it).
        with contextlib.suppress(Exception):
            await r.ping()
        st: Storage = storage or (
            PostgresStorage(settings.database_url) if settings.database_url else NullStorage()
        )
        await st.start()
        app.state.redis = r
        app.state.storage = st
        app.state.engine = Engine(
            redis=r,
            storage=st,
            enricher=Enricher(),
            rules=load_rules(settings.rules_path),
            scorer=_scorer_or_baseline(settings),
            policy=Policy.load(settings.policy_path),
            ip_hmac_key=settings.ip_hmac_key,
        )
        try:
            yield
        finally:
            await st.stop()
            await r.aclose()

    # No interactive docs: the engine is a backend-to-backend service.
    app = FastAPI(title="Gandalf", lifespan=lifespan, docs_url=None, redoc_url=None)

    def auth(x_gandalf_key: Annotated[str | None, Header()] = None) -> None:
        if not x_gandalf_key or not hmac.compare_digest(x_gandalf_key, settings.service_key):
            raise HTTPException(status_code=401, detail="chave de serviço inválida")

    Auth = Depends(auth)

    @app.post("/avaliar", response_model=AssessResponse, dependencies=[Auth])
    async def avaliar(req: AssessRequest, request: Request) -> AssessResponse:
        return await request.app.state.engine.assess(req)

    @app.post("/rotulos", dependencies=[Auth])
    async def rotulos(batch: LabelBatch, request: Request) -> dict[str, int]:
        return {"recebidos": await request.app.state.storage.add_labels(list(batch.rotulos))}

    @app.post("/desafios/{desafio_id}/resultado", dependencies=[Auth])
    async def desafio(desafio_id: str, body: ChallengeResult, request: Request) -> dict[str, str]:
        if not await request.app.state.storage.resolve_challenge(desafio_id, body.estado):
            raise HTTPException(status_code=404, detail="desafio inexistente ou já resolvido")
        return {"desafio_id": desafio_id, "estado": body.estado}

    @app.post("/admin/recarregar-modelo", dependencies=[Auth])
    async def recarregar(request: Request) -> dict[str, str]:
        """Hot reload: the new model is loaded off the event loop and swapped in with
        one assignment, so requests in flight finish on the model they started with."""
        try:
            scorer = await asyncio.to_thread(load_scorer, settings.model_dir)
        except (ModelError, OSError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"modelo recusado: {exc}") from exc
        request.app.state.engine.scorer = scorer
        return {"versao_modelo": scorer.version}

    @app.get("/saude")
    async def saude(request: Request, response: Response) -> dict[str, object]:
        try:
            redis_ok = bool(await request.app.state.redis.ping())
        except Exception:
            redis_ok = False
        db_ok = await request.app.state.storage.healthy()
        if not db_ok:
            response.status_code = 503
        # Redis down is survivable (degraded mode), so it does not fail the probe.
        return {"ok": db_ok, "redis": redis_ok, "banco": db_ok, "modo_degradado": not redis_ok}

    @app.get("/metricas", dependencies=[Auth])
    async def metricas(request: Request) -> Response:
        eng: Engine = request.app.state.engine
        body = eng.metrics.render(request.app.state.storage.dropped)
        return Response(body, media_type="text/plain; version=0.0.4")

    return app


def app() -> FastAPI:  # uvicorn --factory entry point
    return create_app()
