"""Gandalf dashboard: what the engine is deciding, why, how fast, and (for labelled
lab traffic) how often it was right."""

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

JANELAS = {
    "15 minutos": "15 minutes",
    "1 hora": "1 hour",
    "24 horas": "24 hours",
    "7 dias": "7 days",
}
janela = st.sidebar.selectbox("Janela", list(JANELAS), index=1)
INTERVALO = JANELAS[janela]
if st.sidebar.button("Atualizar"):
    st.rerun()

# Colours follow the decision, not the column position.
CORES = {"aprovar": "#2f7d5b", "desafiar": "#c98a1a", "negar": "#b3372e"}
NIVEIS = {
    1: "Nível 1: script e navegador automatizado",
    2: "Nível 2: navegador furtivo",
    3: "Nível 3: agente de IA (LLM)",
    4: "Nível 4: fazenda de contas",
}


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
    no_ar = query(
        """
        SELECT d.versao_modelo, d.versao_features, d.versao_politica
        FROM decisions d JOIN events e USING (avaliacao_id)
        ORDER BY e.recebido_em DESC LIMIT 1
        """,
        INTERVALO,
    )
except psycopg.Error as exc:
    st.error(f"Banco indisponível: {exc}")
    st.stop()

if not no_ar.empty:
    v = no_ar.iloc[0]
    auto, _, fraude = str(v.versao_modelo).partition("+")
    st.sidebar.markdown("**No ar** (última decisão)")
    st.sidebar.markdown(
        f"- Automação: `{auto}`\n- Fraude: `{fraude or '—'}`\n"
        f"- Features: `{v.versao_features}` · Política: `{v.versao_politica}`"
    )

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
serie = por_minuto.pivot_table(index="minuto", columns="decisao", values="n", fill_value=0)
st.bar_chart(serie, color=[CORES[c] for c in serie.columns])

col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("Tipo de ator")
    st.dataframe(
        query(
            """
            SELECT d.ator_tipo AS ator, count(*) AS n
            FROM decisions d JOIN events e USING (avaliacao_id)
            WHERE e.recebido_em > now() - %(i)s::interval GROUP BY 1 ORDER BY 2 DESC
            """,
            INTERVALO,
        ),
        hide_index=True,
        width="stretch",
    )
with col2:
    st.subheader("Como cada desafio é feito")
    st.caption(
        "Passkey quando parece automação (o agente não tem o autenticador do titular); "
        "confirmação fora da sessão quando parece fraude ou o agente saiu do mandato."
    )
    st.dataframe(
        query(
            """
            SELECT d.acao, count(*) AS n
            FROM decisions d JOIN events e USING (avaliacao_id)
            WHERE e.recebido_em > now() - %(i)s::interval AND d.decisao = 'desafiar'
            GROUP BY 1 ORDER BY 2 DESC
            """,
            INTERVALO,
        ),
        hide_index=True,
        width="stretch",
    )
with col3:
    st.subheader("Agentes declarados")
    st.caption("Assinatura verificada (Web Bot Auth): quem decide é o mandato do titular.")
    st.dataframe(
        query(
            """
            SELECT CASE WHEN d.motivos::text LIKE '%%dentro do mandato%%'
                        THEN 'dentro do mandato' ELSE 'fora do mandato' END AS passo,
                   d.decisao, count(*) AS n
            FROM decisions d JOIN events e USING (avaliacao_id)
            WHERE e.recebido_em > now() - %(i)s::interval
              AND d.ator_tipo = 'agente_declarado'
            GROUP BY 1, 2 ORDER BY 1, 2
            """,
            INTERVALO,
        ),
        hide_index=True,
        width="stretch",
    )

# ------------------------------------------------------------ against the answer key
gabarito = query(
    """
    SELECT l.nivel_atacante AS nivel, l.eh_automacao, l.eh_fraude, l.perfil, l.fonte,
           d.decisao
    FROM labels l JOIN decisions d USING (avaliacao_id) JOIN events e USING (avaliacao_id)
    WHERE e.recebido_em > now() - %(i)s::interval
    """,
    INTERVALO,
)
st.subheader("Acerto contra o gabarito (tráfego rotulado do laboratório)")
if gabarito.empty:
    st.caption(
        "Nenhum evento rotulado na janela. O simulador entrega o gabarito ao fim de cada "
        "lote (make simulate, depois make labels RUN=<nome>)."
    )
else:
    st.caption(
        "Decisões tomadas ao vivo, pelos modelos que estavam no ar na hora. Sinalizado = "
        "desafiado ou negado. A avaliação de verdade, com período de teste e intervalos de "
        "confiança, está em docs/results/fase7-final.md."
    )
    g = gabarito.assign(sinalizado=gabarito.decisao != "aprovar")
    real = g.fonte.str.startswith("gravacao")
    humano = (g.nivel == 0) & ~g.eh_automacao & ~g.eh_fraude & ~g.perfil.str.startswith("fraude_")
    linhas = []
    for nv, nome in NIVEIS.items():
        m = g.nivel == nv
        if m.any():
            linhas.append((nome, "pegos", int(m.sum()), g.sinalizado[m].mean()))
    m = g.perfil.str.startswith("fraude_") & g.eh_fraude
    if m.any():
        linhas.append(
            ("Fraude humana (passos fraudulentos)", "pegos", int(m.sum()), g.sinalizado[m].mean())
        )
    m = g.nivel == 5
    if m.any():
        linhas.append(
            ("Agente legítimo (nível 5)", "aprovados", int(m.sum()), 1 - g.sinalizado[m].mean())
        )
    for nome, m in (
        ("Pessoas simuladas", humano & ~real),
        ("Pessoas reais gravadas", humano & real),
    ):
        if m.any():
            linhas.append((nome, "barradas", int(m.sum()), g.sinalizado[m].mean()))
    tabela = pd.DataFrame(linhas, columns=["grupo", "medida", "eventos", "taxa"])
    st.dataframe(
        tabela,
        hide_index=True,
        width="stretch",
        column_config={
            "taxa": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)
        },
    )

st.subheader("Latência por estágio (média, ms)")
st.dataframe(
    query(
        """
        SELECT s.key AS estagio, round(avg(s.value::float)::numeric, 3) AS media_ms
        FROM decisions d JOIN events e USING (avaliacao_id),
             jsonb_each_text(d.estagios_ms) AS s
        WHERE e.recebido_em > now() - %(i)s::interval
        GROUP BY 1 ORDER BY 2 DESC
        """,
        INTERVALO,
    )
    .set_index("estagio")
    .T,
    width="stretch",
)

st.subheader("Últimas decisões")
st.dataframe(
    query(
        """
        SELECT e.recebido_em, e.evento, d.decisao, d.acao, d.ator_tipo,
               round(d.score_automacao::numeric, 3) AS score_automacao,
               round(d.score_fraude::numeric, 3) AS score_fraude,
               d.motivos, d.regras_acionadas, e.tipo_rede, e.ua_familia, e.ja4,
               d.versao_modelo, round(d.latencia_ms::numeric, 2) AS latencia_ms, d.avaliacao_id
        FROM decisions d JOIN events e USING (avaliacao_id)
        WHERE e.recebido_em > now() - %(i)s::interval
        ORDER BY e.recebido_em DESC LIMIT 200
        """,
        INTERVALO,
    ),
    hide_index=True,
    width="stretch",
)
