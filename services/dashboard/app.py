"""Gandalf dashboard (phase 1): decisions per minute, latency, latest decisions."""

from __future__ import annotations

import os

import pandas as pd
import psycopg
import streamlit as st

DSN = os.environ.get(
    "DASHBOARD_DATABASE_URL", "postgresql://gandalf:gandalf@localhost:5432/gandalf"
)

st.set_page_config(page_title="Gandalf — AgentGate", layout="wide")
st.title("Gandalf — AgentGate")
st.caption("Quem está agindo, em nome de quem, e com qual autorização?")

janela = st.sidebar.selectbox("Janela", ["15 minutos", "1 hora", "24 horas"], index=1)
INTERVALO = {"15 minutos": "15 minutes", "1 hora": "1 hour", "24 horas": "24 hours"}[janela]
if st.sidebar.button("Atualizar"):
    st.rerun()


@st.cache_data(ttl=5)
def query(sql: str, intervalo: str) -> pd.DataFrame:
    with psycopg.connect(DSN, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute(sql, {"i": intervalo})
        cols = [c.name for c in cur.description or []]
        return pd.DataFrame(cur.fetchall(), columns=cols)


try:
    resumo = query(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE d.decisao = 'aprovar')  AS aprovadas,
               count(*) FILTER (WHERE d.decisao = 'desafiar') AS desafiadas,
               count(*) FILTER (WHERE d.decisao = 'negar')    AS negadas,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY d.latencia_ms) AS p50,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY d.latencia_ms) AS p99,
               count(*) FILTER (WHERE d.modo_degradado) AS degradadas
        FROM decisions d JOIN events e USING (avaliacao_id)
        WHERE e.recebido_em > now() - %(i)s::interval
        """,
        INTERVALO,
    )
except psycopg.Error as exc:
    st.error(f"Banco indisponível: {exc}")
    st.stop()

r = resumo.iloc[0]
c = st.columns(6)
c[0].metric("Avaliações", int(r.total))
c[1].metric("Aprovadas", int(r.aprovadas))
c[2].metric("Desafiadas", int(r.desafiadas))
c[3].metric("Negadas", int(r.negadas))
c[4].metric("Latência p50", f"{r.p50:.1f} ms" if pd.notna(r.p50) else "—")
c[5].metric("Latência p99", f"{r.p99:.1f} ms" if pd.notna(r.p99) else "—")
if int(r.degradadas):
    st.warning(f"{int(r.degradadas)} decisões em modo degradado (Redis indisponível).")

if int(r.total) == 0:
    st.info("Sem eventos na janela. Abra a loja em https://localhost:8443 e crie uma conta.")
    st.stop()

por_minuto = query(
    """
    SELECT date_trunc('minute', e.recebido_em) AS minuto, d.decisao, count(*) AS n
    FROM decisions d JOIN events e USING (avaliacao_id)
    WHERE e.recebido_em > now() - %(i)s::interval
    GROUP BY 1, 2 ORDER BY 1
    """,
    INTERVALO,
)
st.subheader("Decisões por minuto")
# Colours follow the decision, not the column position.
CORES = {"aprovar": "#2f7d5b", "desafiar": "#c98a1a", "negar": "#b3372e"}
serie = por_minuto.pivot_table(index="minuto", columns="decisao", values="n", fill_value=0)
st.bar_chart(serie, color=[CORES[c] for c in serie.columns])

esq, dir_ = st.columns(2)
with esq:
    st.subheader("Latência por estágio (média, ms)")
    estagios = query(
        """
        SELECT s.key AS estagio, avg(s.value::float) AS media_ms
        FROM decisions d JOIN events e USING (avaliacao_id),
             jsonb_each_text(d.estagios_ms) AS s
        WHERE e.recebido_em > now() - %(i)s::interval
        GROUP BY 1 ORDER BY 2 DESC
        """,
        INTERVALO,
    )
    st.dataframe(estagios, hide_index=True, width="stretch")
with dir_:
    st.subheader("Tipo de ator")
    atores = query(
        """
        SELECT d.ator_tipo, count(*) AS n
        FROM decisions d JOIN events e USING (avaliacao_id)
        WHERE e.recebido_em > now() - %(i)s::interval GROUP BY 1 ORDER BY 2 DESC
        """,
        INTERVALO,
    )
    st.dataframe(atores, hide_index=True, width="stretch")

st.subheader("Últimas decisões")
ultimas = query(
    """
    SELECT e.recebido_em, e.evento, d.decisao, d.acao, d.ator_tipo,
           round(d.score_automacao::numeric, 3) AS score_automacao,
           e.tipo_rede, e.ua_familia, e.ja4, d.motivos, d.regras_acionadas,
           d.versao_modelo, round(d.latencia_ms::numeric, 2) AS latencia_ms, d.avaliacao_id
    FROM decisions d JOIN events e USING (avaliacao_id)
    WHERE e.recebido_em > now() - %(i)s::interval
    ORDER BY e.recebido_em DESC LIMIT 200
    """,
    INTERVALO,
)
st.dataframe(ultimas, hide_index=True, width="stretch")
