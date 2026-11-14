"""Retention and the right to deletion, against the lab database (docs/lgpd.md).

Everything runs inside one transaction that is rolled back: the lab's data is untouched.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from jobs.retention import erase_participant, expire

pytestmark = pytest.mark.e2e

DSN = "postgresql://gandalf:gandalf@localhost:5432/gandalf"


def _event(conn: psycopg.Connection, age_days: int) -> str:
    aid = "av_test_" + uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO events (avaliacao_id, id_requisicao, recebido_em, momento, evento,
                            dispositivo_id, sessao_id, ip_hmac, ip_prefixo, telemetria, sessao)
        VALUES (%s, %s, now() - make_interval(days => %s), now(), 'login',
                'd', 's', 'h', '10.0.0.0/24', '{}', '{}')
        """,
        (aid, aid, age_days),
    )
    conn.execute(
        "INSERT INTO feature_snapshots (avaliacao_id, versao_features, features) "
        "VALUES (%s, 'f4', '{}')",
        (aid,),
    )
    return aid


def _label(conn: psycopg.Connection, aid: str, fonte: str, perfil: str) -> None:
    conn.execute(
        "INSERT INTO labels (avaliacao_id, eh_automacao, eh_fraude, ator_real, perfil, fonte) "
        "VALUES (%s, false, false, 'humano', %s, %s)",
        (aid, perfil, fonte),
    )


def _exists(conn: psycopg.Connection, aid: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM events WHERE avaliacao_id = %s", (aid,)).fetchone() is not None
    )


def test_retention_drops_old_real_traffic_and_keeps_training_data() -> None:
    with psycopg.connect(DSN) as conn:
        try:
            old_real, new_real = _event(conn, 31), _event(conn, 2)
            old_sim = _event(conn, 31)
            _label(conn, old_sim, "navegador:cliente_novo", "padrao")
            expire(conn, 30)
            assert not _exists(conn, old_real)
            assert (
                conn.execute(
                    "SELECT 1 FROM feature_snapshots WHERE avaliacao_id = %s", (old_real,)
                ).fetchone()
                is None
            ), "the feature vector goes with the event"
            assert _exists(conn, new_real)
            assert _exists(conn, old_sim), "labelled lab traffic is the training set"
        finally:
            conn.rollback()


def test_a_volunteer_can_be_erased_by_their_code() -> None:
    code = "T" + uuid.uuid4().hex[:6].upper()
    with psycopg.connect(DSN) as conn:
        try:
            mine, other = _event(conn, 1), _event(conn, 1)
            _label(conn, mine, "gravacao:humano_real", f"real_{code}")
            _label(conn, other, "gravacao:humano_real", "real_OUTRA")
            out = erase_participant(conn, code.lower())
            assert out == {"eventos": 1, "rotulos": 1}
            assert not _exists(conn, mine)
            assert _exists(conn, other)
        finally:
            conn.rollback()
