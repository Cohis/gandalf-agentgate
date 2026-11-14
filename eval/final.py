"""Phase 7: the final evaluation of the whole system, as the engine decides.

    uv run python -m eval.final [--cohort f4]

It rebuilds the two models from scratch on one data campaign (one feature set), with
the same rules the engine uses (automation threshold at 1 % false positives on
validation, fraud thresholds by cost), and reports on the most recent period:

1. The ladder: each attacker level, human fraud, the legitimate agent, simulated
   people (by persona) and real recorded people (one person left out at a time).
2. Blocking by IP only, the baseline every naive system starts with.
3. Ablation: the same system with one layer of signals removed at a time.

Level 5 (the declared agent) is not decided by the models but by the policy (its
signature and mandate), so its rows come from the engine's own logged decisions.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import polars as pl
import psycopg
from gandalf_features import FEATURE_NAMES

from jobs.train import (
    ABSOLUTE_PACE,
    DSN,
    NOT_YET_TRUSTED,
    TARGET_FPR,
    assign_people,
    boot_ci,
    fit,
    load,
    people,
    stratified_split,
    threshold_at_fpr,
)
from jobs.train_fraud import FRAUD_FEATURES, as_fraud, cost_thresholds, costs, farm_curve

OUT = Path("eval/out")

LAYERS: dict[str, tuple[str, ...]] = {
    "Ambiente (navegador, user-agent, JA4)": (
        "tel_coletada", "webdriver", "inconsistencias_fp", "ua_", "ja4_ausente",
    ),
    "Ritmo e ponteiro": ("ritmo_", "cursor_linear", "cliques_centro", "digitacao_", "toque", "foco_teclado"),
    "Forma da sessão": ("sessao_",),
    "Velocidade e escala": ("ev_", "cadastros_", "contas_", "disps_", "cartoes_", "escala_"),
    "Armadilhas (honeypot, canário)": ("honeypot", "canario"),
    "Rede e reputação de IP": ("rede_", "ip_em_lista", "ip_reputacao"),
}  # fmt: skip
IP_ONLY = [
    n for n in FEATURE_NAMES
    if n.startswith(("rede_", "ev_ip_", "contas_por_ip", "cartoes_por_ip", "cadastros_ip"))
    or n in {"ip_em_lista", "ip_reputacao"}
]  # fmt: skip
ACCESSIBILITY = {"leitor_tela", "controle_voz"}


def _in_layer(name: str, layer: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p) for p in layer)


def system(train: pl.DataFrame, valid: pl.DataFrame, drop: tuple[str, ...] = ()) -> tuple:
    """Both models and their thresholds, built the way jobs/train*.py build them."""
    auto_feats = [
        n for n in FEATURE_NAMES
        if n not in ABSOLUTE_PACE and n not in NOT_YET_TRUSTED and not _in_layer(n, drop)
    ]  # fmt: skip
    fraud_feats = [n for n in FRAUD_FEATURES if not _in_layer(n, drop)]
    auto = fit(train, valid, auto_feats)
    xv, yv = valid.select(auto_feats).to_numpy(), valid["y"].to_numpy().astype(int)
    t_auto = threshold_at_fpr(auto.predict(xv)[yv == 0], TARGET_FPR)
    ftrain, fvalid = as_fraud(train), as_fraud(valid)
    fraud = fit(ftrain, fvalid, fraud_feats)
    sv = fraud.predict(fvalid.select(fraud_feats).to_numpy())
    _, t1, t2 = cost_thresholds(sv, fvalid["y"].to_numpy().astype(int), costs())
    return auto, auto_feats, t_auto, fraud, fraud_feats, t1, t2


def decide_auto(sys: tuple, part: pl.DataFrame) -> np.ndarray:
    """The automation model alone: the only one the recordings can test (see as_fraud)."""
    auto, af, ta, *_ = sys
    if not part.height:
        return np.zeros(0, dtype=bool)
    return auto.predict(part.select(af).to_numpy()) >= ta


def decide(sys: tuple, part: pl.DataFrame) -> np.ndarray:
    """True where the engine would challenge or deny."""
    auto, af, ta, fraud, ff, t1, _ = sys
    if not part.height:
        return np.zeros(0, dtype=bool)
    return (auto.predict(part.select(af).to_numpy()) >= ta) | (
        fraud.predict(part.select(ff).to_numpy()) >= t1
    )


def row(label: str, flags: np.ndarray, sess: np.ndarray, rng, kind: str) -> str:  # type: ignore[no-untyped-def]
    if not len(flags):
        return f"| {label} | 0 | 0 | — | — |"
    lo, hi = boot_ci(flags, sess, rng)
    rate = flags.mean() if kind != "aprovado" else 1 - flags.mean()
    if kind == "aprovado":
        lo, hi = 1 - hi, 1 - lo
    return f"| {label} | {len(np.unique(sess))} | {len(flags)} | {rate:.1%} | {lo:.1%} a {hi:.1%} |"


def groups(test: pl.DataFrame) -> list[tuple[str, np.ndarray, str]]:
    nivel, perfil = test["nivel"].to_numpy(), test["perfil"].to_numpy().astype(str)
    y, yf = test["y"].to_numpy().astype(int), test["yf"].to_numpy().astype(int)
    fraudster = np.char.startswith(perfil, "fraude_")
    out = [
        (f"Atacante nível {lv}", nivel == lv, "detectado")
        for lv in (1, 2, 3, 4)
        if (nivel == lv).any()
    ]
    out.append(("Fraude humana (passos fraudulentos)", fraudster & (yf == 1), "detectado"))
    honest = (y == 0) & (yf == 0) & ~fraudster & (nivel == 0)
    out.append(("Pessoas simuladas, todas", honest, "barrado"))
    if "onda" in test.columns:
        wave = test["onda"].to_numpy().astype(bool)
        out.append(("Pessoas simuladas, dia comum", honest & ~wave, "barrado"))
        out.append(("Pessoas simuladas, durante uma fazenda", honest & wave, "barrado"))
    out.append(
        (
            "Pessoas simuladas com deficiência",
            honest & np.isin(perfil, list(ACCESSIBILITY)),
            "barrado",
        )
    )
    out.append(
        (
            "Pessoas simuladas no celular (CGNAT)",
            honest & np.isin(perfil, ["movel_cgnat", "celular"]),
            "barrado",
        )
    )
    return out


def deny_rule_ids() -> list[str]:
    import gandalf_engine
    from gandalf_contracts import Decisao
    from gandalf_engine.rules import load_rules

    path = Path(gandalf_engine.__file__).with_name("rules.yaml")
    return [r.id for r in load_rules(path) if r.decisao is Decisao.negar]


def level5(cohort: str) -> list[str]:
    """Replays the declared-agent policy on what the engine logged: the identity layer's
    verdict (inside or outside the mandate) and any hard deny rule. The models are not
    consulted for a verified agent, so the candidates do not change these rows."""
    sql = """
        SELECT CASE WHEN d.motivos::text LIKE '%%dentro do mandato%%' THEN 'dentro'
                    ELSE 'fora' END,
               e.sessao_id,
               CASE WHEN d.regras_acionadas ?| %s THEN 'negar'
                    WHEN d.motivos::text LIKE '%%dentro do mandato%%' THEN 'aprovar'
                    ELSE 'desafiar' END
        FROM labels l JOIN events e USING (avaliacao_id) JOIN decisions d USING (avaliacao_id)
        JOIN feature_snapshots f USING (avaliacao_id)
        WHERE l.nivel_atacante = 5 AND f.versao_features = %s
    """
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(sql, (deny_rule_ids(), cohort)).fetchall()
    if not rows:
        return ["| Agente legítimo (nível 5) | — | — | sem dados | |"]
    rng = np.random.default_rng(5)
    out = []
    for step, label, kind in (
        ("dentro", "Agente legítimo, passos dentro do mandato (aprovado)", "aprovado"),
        ("fora", "Agente legítimo, passos fora do mandato (titular consultado)", "detectado"),
    ):
        r = [x for x in rows if x[0] == step]
        flags = np.array([x[2] != "aprovar" for x in r], dtype=bool)
        out.append(row(label, flags, np.array([x[1] for x in r]), rng, kind))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cohort", default="f4", help="feature-set campaign to evaluate")
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    t0 = time.monotonic()

    everything = load()
    sim = stratified_split(
        everything.filter(~pl.col("real") & (pl.col("vf") == a.cohort) & (pl.col("nivel") != 5))
    )
    real = everything.filter(pl.col("real"))
    real = assign_people(real, people(real))
    if not sim.height:
        raise SystemExit(f"no simulated data in cohort {a.cohort}")
    df = pl.concat([sim, real], how="diagonal")
    train, valid = (df.filter(pl.col("conjunto") == c) for c in ("treino", "validacao"))
    test = sim.filter(pl.col("conjunto") == "teste")
    full = system(train, valid)

    lines = [
        f"# Avaliação final ({a.cohort})",
        "",
        f"Gerado em {time.strftime('%Y-%m-%d %H:%M')}. {sim.height} eventos simulados nesta campanha "
        f"({test.height} no período de teste) e {real.height} eventos de {real['perfil'].n_unique()} "
        "pessoas reais. Decisão como o motor toma: desafio ou negação = sinalizado.",
        "",
        "## A escada",
        "",
        "| Grupo | Sessões | Eventos | Taxa | IC 95 % |",
        "|---|---|---|---|---|",
    ]
    sess = test["sessao_id"].to_numpy()
    flags = decide(full, test)
    for label, mask, kind in groups(test):
        lines.append(row(f"{label} ({kind})", flags[mask], sess[mask], rng, kind))
    lines += level5(a.cohort)

    # real people, one left out at a time, with the full system
    names = people(real)
    rf, rs, rp = [], [], []
    for held in names:
        rest = assign_people(real.filter(pl.col("perfil") != held), [n for n in names if n != held])
        s_ = system(
            pl.concat(
                [
                    sim.filter(pl.col("conjunto") == "treino"),
                    rest.filter(pl.col("conjunto") == "treino"),
                ],
                how="diagonal",
            ),
            pl.concat(
                [
                    sim.filter(pl.col("conjunto") == "validacao"),
                    rest.filter(pl.col("conjunto") == "validacao"),
                ],
                how="diagonal",
            ),
        )
        part = real.filter(pl.col("perfil") == held)
        rf.append(decide_auto(s_, part))
        rs.append(part["sessao_id"].to_numpy())
        rp.append(part["toque"].fill_null(0).to_numpy() >= 0.5)
    if names:
        f_, s_, p_ = (np.concatenate(x) for x in (rf, rs, rp))
        lines.append(
            row(
                f"Pessoas reais, {len(names)} voluntários, uma de fora por vez (barrado)",
                f_,
                s_,
                rng,
                "barrado",
            )
        )
        lines.append(row("Pessoas reais no celular (barrado)", f_[p_], s_[p_], rng, "barrado"))
        never = decide_auto(
            system(
                sim.filter(pl.col("conjunto") == "treino"),
                sim.filter(pl.col("conjunto") == "validacao"),
            ),
            real,
        )
        lines.append(
            row(
                "Pessoas reais, sem nenhuma pessoa real no treino (barrado)",
                never,
                real["sessao_id"].to_numpy(),
                rng,
                "barrado",
            )
        )
        lines += [
            "",
            "As linhas de pessoas reais medem só o modelo de automação. O de fraude lê rede, "
            "velocidade e valor, e nas gravações isso é o arranjo da gravação (todos pelo "
            "túnel, rede desconhecida, uma lista de tarefas em poucos minutos), não o "
            "comportamento de um cliente; por isso as gravações não entram no treino dele.",
        ]

    # baseline: IP only
    honest_v = ~pl.col("y").cast(pl.Boolean) & ~pl.col("yf").cast(pl.Boolean)
    ipm = fit(train, valid, IP_ONLY)
    t_ip = threshold_at_fpr(
        ipm.predict(valid.filter(honest_v).select(IP_ONLY).to_numpy()), TARGET_FPR
    )
    ip_flags = ipm.predict(test.select(IP_ONLY).to_numpy()) >= t_ip
    lines += [
        "",
        "## Bloquear só por IP",
        "",
        f"Um modelo que só vê o IP ({len(IP_ONLY)} features: tipo de rede, listas, reputação e "
        "contagens por IP), com o mesmo limiar de 1 % de falso positivo na validação.",
        "",
        "| Grupo | Sessões | Eventos | Só IP | Motor completo |",
        "|---|---|---|---|---|",
    ]
    for label, mask, kind in groups(test):
        if mask.any():
            r_ip = ip_flags[mask].mean()
            r_full = flags[mask].mean()
            lines.append(
                f"| {label} ({kind}) | {len(np.unique(sess[mask]))} | {int(mask.sum())} | {r_ip:.1%} | {r_full:.1%} |"
            )

    # a farm the system has never seen
    farms = sorted(sim.filter(pl.col("nivel") == 4)["perfil"].unique().to_list())
    if len(farms) > 1:
        lines += [
            "",
            "## Fazenda nunca vista",
            "",
            "O sistema inteiro (os dois modelos, os dois limiares) refeito sem nenhuma conta de "
            "uma fazenda e aplicado a todas as contas dela, na ordem em que foram criadas. "
            "Uma conta conta como pega se qualquer passo dela foi desafiado ou negado.",
            "",
            "| Fazenda nunca vista | Contas | Pegas | Das 20 primeiras | Primeira pega |",
            "|---|---|---|---|---|",
        ]
        for fz in farms:
            keep = pl.col("perfil") != fz
            blind = system(train.filter(keep), valid.filter(keep))
            part = sim.filter(pl.col("perfil") == fz)
            cv = farm_curve(part, decide(blind, part))
            lines.append(
                f"| {fz.removeprefix('fazenda_llm_')} | {cv['contas']} | {cv['pegas']:.0%} | "
                f"{cv['primeiras']:.0%} | {cv['primeira_pega'] or '—'} |"
            )

    # ablation
    lines += [
        "",
        "## Ablação: o sistema sem uma camada de cada vez",
        "",
        "Os dois modelos são refeitos sem as features da camada. Detecção = média sobre os "
        "grupos de ataque; pessoas barradas = pessoas simuladas honestas.",
        "",
        "| Sem a camada | Detecção | Pessoas barradas | Com deficiência barradas |",
        "|---|---|---|---|",
    ]
    gs = groups(test)
    attack = [m for lbl, m, k in gs if k == "detectado" and m.any()]
    honest = next(m for lbl, m, k in gs if lbl == "Pessoas simuladas, todas")
    acc = next(m for lbl, m, k in gs if lbl == "Pessoas simuladas com deficiência")

    def summary(fl: np.ndarray) -> str:
        det = np.mean([fl[m].mean() for m in attack])
        a_ = f"{fl[acc].mean():.1%}" if acc.any() else "—"
        return f"{det:.1%} | {fl[honest].mean():.1%} | {a_}"

    lines.append(f"| (nada: sistema completo) | {summary(flags)} |")
    for name, layer in LAYERS.items():
        lines.append(f"| {name} | {summary(decide(system(train, valid, layer), test))} |")

    lines += ["", f"Tempo de cálculo: {time.monotonic() - t0:.0f} s.", ""]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"final-{a.cohort}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
