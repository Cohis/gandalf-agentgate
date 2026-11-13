"""PostgreSQL persistence, kept off the request's critical path.

The request handler only enqueues; a background task batches the inserts. If the
queue is full the record is dropped and counted, never blocking a decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from gandalf_contracts import LabelIn

log = logging.getLogger("gandalf.storage")


@dataclass(slots=True)
class Record:
    event: dict[str, Any]
    decision: dict[str, Any]
    features: dict[str, Any]
    embedding: list[float] | None = None  # sign-up text vector; the text is never stored


class Storage(Protocol):
    dropped: int

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def enqueue(self, record: Record) -> None: ...
    async def add_labels(self, labels: list[LabelIn]) -> int: ...
    async def resolve_challenge(self, desafio_id: str, estado: str) -> bool: ...
    async def healthy(self) -> bool: ...


class NullStorage:
    """Used when no database is configured (unit tests). Keeps records in memory."""

    def __init__(self) -> None:
        self.dropped = 0
        self.records: list[Record] = []
        self.labels: list[LabelIn] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def enqueue(self, record: Record) -> None:
        self.records.append(record)

    async def add_labels(self, labels: list[LabelIn]) -> int:
        self.labels.extend(labels)
        return len(labels)

    async def resolve_challenge(self, desafio_id: str, estado: str) -> bool:
        return any(r.decision.get("desafio_id") == desafio_id for r in self.records)

    async def healthy(self) -> bool:
        return True


_EVENT_COLS = (
    "avaliacao_id", "id_requisicao", "momento", "evento", "conta_id", "dispositivo_id",
    "sessao_id", "ip_hmac", "ip_prefixo", "asn", "pais", "tipo_rede", "ja4", "http2_fp",
    "ua_familia", "cartao_hash", "valor", "telemetria", "sessao", "ator_declarado",
)  # fmt: skip
_DECISION_COLS = (
    "avaliacao_id", "decisao", "acao", "ator_tipo", "ator_confianca", "score_automacao",
    "score_fraude", "motivos", "regras_acionadas", "desafio_id", "versao_modelo",
    "versao_features", "versao_politica", "modo_degradado", "latencia_ms", "estagios_ms",
)  # fmt: skip


def _insert(table: str, cols: tuple[str, ...]) -> str:
    names = ", ".join(cols)
    params = ", ".join(f"%({c})s" for c in cols)
    return f"INSERT INTO {table} ({names}) VALUES ({params}) ON CONFLICT DO NOTHING"  # noqa: S608


class PostgresStorage:
    def __init__(self, dsn: str, *, max_queue: int = 20_000, batch: int = 200) -> None:
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(dsn, min_size=1, max_size=4, open=False)
        self._queue: asyncio.Queue[Record] = asyncio.Queue(maxsize=max_queue)
        self._batch = batch
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0

    async def start(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        self._task = asyncio.create_task(self._run(), name="gandalf-storage")

    async def stop(self) -> None:
        if self._task:
            await self._queue.join()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._pool.close()

    def enqueue(self, record: Record) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._batch and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await self._flush(batch)
            except Exception:
                self.dropped += len(batch)
                log.exception("failed to persist %d records", len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _flush(self, batch: list[Record]) -> None:
        from psycopg.types.json import Jsonb

        def wrap(row: dict[str, Any]) -> dict[str, Any]:
            return {k: Jsonb(v) if isinstance(v, dict | list) else v for k, v in row.items()}

        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(_insert("events", _EVENT_COLS), [wrap(r.event) for r in batch])
            await cur.executemany(
                _insert("decisions", _DECISION_COLS), [wrap(r.decision) for r in batch]
            )
            await cur.executemany(
                "INSERT INTO feature_snapshots (avaliacao_id, versao_features, features) "
                "VALUES (%(avaliacao_id)s, %(versao_features)s, %(features)s) "
                "ON CONFLICT DO NOTHING",
                [wrap(r.features) for r in batch],
            )
            embeddings = [
                {
                    "avaliacao_id": r.event["avaliacao_id"],
                    "momento": r.event["momento"],
                    "vetor": r.embedding,
                }
                for r in batch
                if r.embedding is not None
            ]
            if embeddings:
                await cur.executemany(
                    "INSERT INTO signup_embeddings (avaliacao_id, momento, vetor) "
                    "VALUES (%(avaliacao_id)s, %(momento)s, %(vetor)s) ON CONFLICT DO NOTHING",
                    embeddings,
                )
            challenges = [r.decision for r in batch if r.decision["desafio_id"]]
            if challenges:
                await cur.executemany(
                    "INSERT INTO challenges (desafio_id, avaliacao_id, tipo) "
                    "VALUES (%(desafio_id)s, %(avaliacao_id)s, %(acao)s) ON CONFLICT DO NOTHING",
                    challenges,
                )

    async def add_labels(self, labels: list[LabelIn]) -> int:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO labels (avaliacao_id, eh_automacao, eh_fraude, ator_real, "
                "nivel_atacante, perfil, fonte) VALUES (%(avaliacao_id)s, %(eh_automacao)s, "
                "%(eh_fraude)s, %(ator_real)s, %(nivel_atacante)s, %(perfil)s, %(fonte)s) "
                "ON CONFLICT (avaliacao_id) DO NOTHING",
                [lb.model_dump(mode="json") for lb in labels],
            )
        return len(labels)

    async def resolve_challenge(self, desafio_id: str, estado: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE challenges SET estado = %s, resolvido_em = now() "
                "WHERE desafio_id = %s AND estado = 'pendente'",
                (estado, desafio_id),
            )
            return cur.rowcount == 1

    async def healthy(self) -> bool:
        try:
            async with self._pool.connection(timeout=1) as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False
