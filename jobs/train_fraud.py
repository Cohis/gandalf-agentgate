"""Train and evaluate the 'is this fraud?' model, and choose its thresholds by cost.

    uv run python -m jobs.train_fraud            # train, evaluate, write the report
    uv run python -m jobs.train_fraud --activate # ...and make it the engine's fraud model

The automation model asks HOW someone acts (pointer, rhythm, environment). This one
asks WHAT is being done: how many accounts this device has created, how many cards
it has tried, how much is being spent. It never sees the behavioural features, so:

- it can catch a person committing fraud by hand, which the automation model must
  not do (people are what it protects);
- in phase 5 a declared, authorised agent will be judged on what it does, not on
  being a bot.

Thresholds are chosen on the validation period to minimise the expected cost of
mistakes, with the costs in services/engine/src/gandalf_engine/policy.yaml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import polars as pl
import psycopg
import yaml
from gandalf_engine.scoring import LightGBMScorer
from gandalf_features import FEATURE_NAMES, FEATURE_VERSION
from sklearn.metrics import average_precision_score

from jobs.train import (
    DSN,
    MODELS,
    REPORT,
    TARGET_FPR,
    assign_people,
    fit,
    load,
    people,
    rate_row,
    temporal_split,
    threshold_at_fpr,
)

POLICY = Path("services/engine/src/gandalf_engine/policy.yaml")
_VELOCITY = ("ev_", "cadastros_", "contas_", "disps_", "cartoes_")
FRAUD_FEATURES = [
    n
    for n in FEATURE_NAMES
    if n.startswith(_VELOCITY)
    or n.startswith(("evento_", "rede_"))
    or n in {"valor", "ip_em_lista", "ip_reputacao"}
]


def costs() -> dict[str, float]:
    raw = yaml.safe_load(POLICY.read_text(encoding="utf-8"))["custos"]
    return {k: float(v) for k, v in raw.items()}


def decision_cost(flag: np.ndarray, deny: np.ndarray, y: np.ndarray, c: dict[str, float]) -> float:
    """Cost per event. ``flag``: challenged or denied. Per-class rates are weighted by
    the assumed real-world prevalence of fraud, so the lab's attack-heavy mix does
    not decide where the thresholds go."""
    fraud, honest = y == 1, y == 0
    challenge = flag & ~deny
    per_fraud = (
        c["custo_fn"] * (~flag & fraud).sum()
        + (c["custo_desafio"] + (1 - c["desafio_barra"]) * c["custo_fn"])
        * (challenge & fraud).sum()
    ) / max(int(fraud.sum()), 1)
    per_honest = (
        c["custo_desafio"] * (challenge & honest).sum() + c["custo_fp"] * (deny & honest).sum()
    ) / max(int(honest.sum()), 1)
    pi = c["prevalencia_fraude"]
    return float(pi * per_fraud + (1 - pi) * per_honest)


def expected_cost(s: np.ndarray, y: np.ndarray, t1: float, t2: float, c: dict[str, float]) -> float:
    """Approve below t1, challenge in [t1, t2), deny from t2."""
    return decision_cost(s >= t1, s >= t2, y, c)


def cost_thresholds(
    s: np.ndarray, y: np.ndarray, c: dict[str, float]
) -> tuple[float, float, float]:
    grid = np.unique(np.concatenate([[0.0, 1.0 + 1e-9], np.quantile(s, np.linspace(0, 1, 201))]))
    best = (float("inf"), 1.0, 1.0)
    for i, t1 in enumerate(grid):
        for t2 in grid[i:]:
            cost = expected_cost(s, y, t1, t2, c)
            if cost < best[0]:
                best = (cost, float(t1), float(t2))
    return best


def as_fraud(df: pl.DataFrame) -> pl.DataFrame:
    """The shared helpers read the label from column ``y``."""
    return df.with_columns(pl.col("yf").alias("y"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--activate", action="store_true", help="point models/ACTIVE_FRAUDE at it")
    a = ap.parse_args()
    rng = np.random.default_rng(11)
    c = costs()

    everything = load()
    sim = temporal_split(everything.filter(~pl.col("real")))
    real = assign_people(
        everything.filter(pl.col("real")), people(everything.filter(pl.col("real")))
    )
    df = as_fraud(pl.concat([sim, real], how="diagonal"))
    train, valid, test = (
        df.filter(pl.col("conjunto") == k) for k in ("treino", "validacao", "teste")
    )
    model = fit(train, valid, FRAUD_FEATURES)

    def score(m, part: pl.DataFrame) -> np.ndarray:  # type: ignore[no-untyped-def]
        return m.predict(part.select(FRAUD_FEATURES).to_numpy())

    sv, yv = score(model, valid), valid["y"].to_numpy().astype(int)
    st, yt = score(model, test), test["y"].to_numpy().astype(int)
    val_cost, t1, t2 = cost_thresholds(sv, yv, c)
    fpr_thr = threshold_at_fpr(sv[yv == 0], TARGET_FPR)

    # Crossed with the automation model that is live, as the engine would do it.
    auto_path = MODELS / (MODELS / "ACTIVE").read_text(encoding="utf-8").strip()
    auto = LightGBMScorer(auto_path)
    ta = auto.thresholds
    auto_scores = np.array(
        [
            auto.score(dict(zip(FEATURE_NAMES, row, strict=True)))[0]
            for row in test.select(FEATURE_NAMES).iter_rows()
        ]
    )
    auto_flag = auto_scores >= (ta.desafiar if ta else 0.6)

    def cost_of(flag: np.ndarray, deny: np.ndarray) -> float:
        return decision_cost(flag, deny, yt, c) * 1000

    fraud_flag, fraud_deny = st >= t1, st >= t2
    none = np.zeros_like(auto_flag)
    nothing = cost_of(none, none)
    only_auto = cost_of(auto_flag, none)
    only_fraud = cost_of(fraud_flag, fraud_deny)
    both = cost_of(auto_flag | fraud_flag, fraud_deny)

    perfil, sess = test["perfil"].to_numpy(), test["sessao_id"].to_numpy()
    nivel = test["nivel"].to_numpy()
    flagged = st >= t1
    combined = flagged | auto_flag

    def rows(label: str, mask: np.ndarray, flags: np.ndarray) -> str:
        return rate_row(label, flags[mask], sess[mask], rng)

    groups = [
        ("Fraude humana: " + pf.removeprefix("fraude_"), (perfil == pf) & (yt == 1))
        for pf in sorted({p for p in perfil if p.startswith("fraude_")})
    ]
    groups += [
        (f"Atacante nível {lv}", (nivel == lv) & (yt == 1))
        for lv in sorted(set(nivel[yt == 1]) - {0})
    ]
    fraudster = np.char.startswith(perfil.astype(str), "fraude_")
    groups += [
        ("Pessoas honestas, simuladas", (yt == 0) & ~test["real"].to_numpy() & ~fraudster),
        ("Passos não fraudulentos de fraudadores", (yt == 0) & fraudster),
        ("Pessoas reais gravadas", (yt == 0) & test["real"].to_numpy()),
    ]

    stamp = time.strftime("%Y%m%d-%H%M")
    digest = hashlib.sha256(model.booster.model_to_string().encode()).hexdigest()[:6]
    versao = f"fr-{stamp}-{digest}"
    out = MODELS / versao
    out.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(out / "model.txt", num_iteration=model.booster.best_iteration)
    metrics = {
        "pr_auc_teste": float(average_precision_score(yt, st)) if yt.any() else None,
        "custo_por_mil": {
            "nada": nothing,
            "so_automacao": only_auto,
            "so_fraude": only_fraud,
            "ambos": both,
        },
    }
    meta = {
        "versao": versao,
        "alvo": "fraude",
        "versao_features": FEATURE_VERSION,
        "features": FRAUD_FEATURES,
        "calibracao": {"a": model.a, "b": model.b},
        "limiares": {"desafiar": t1, "negar": t2},
        "custos": c,
        "metricas": metrics,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    gain = sorted(
        zip(FRAUD_FEATURES, model.booster.feature_importance("gain"), strict=True),
        key=lambda kv: -kv[1],
    )
    total_gain = sum(g for _, g in gain) or 1.0
    n_fraud = int(df["y"].sum())
    lines = [
        f"# Modelo de fraude `{versao}`",
        "",
        f"Gerado em {time.strftime('%Y-%m-%d %H:%M')}. Features `{FEATURE_VERSION}`, só as de "
        f"velocidade e transação ({len(FRAUD_FEATURES)}): o modelo nunca vê mouse, ritmo nem "
        f"ambiente. {df.height} eventos, {n_fraud} rotulados como fraude.",
        "",
        "## Limiares escolhidos por custo",
        "",
        f"Custos da política: fraude aprovada {c['custo_fn']:g}, cliente honesto negado "
        f"{c['custo_fp']:g}, desafio {c['custo_desafio']:g}; um desafio barra "
        f"{c['desafio_barra']:.0%} das fraudes. Os custos são calculados como se "
        f"{c['prevalencia_fraude']:.0%} do tráfego fosse fraude (uma suposição da política): o "
        "laboratório tem muito mais ataques do que clientes, e sem essa correção desafiar todo "
        "mundo pareceria barato.",
        "",
        f"- Desafiar (confirmação fora da sessão) a partir de **{t1:.4f}**.",
        f"- Negar a partir de **{t2:.4f}**." + (" Na prática nunca nega." if t2 > 1 else ""),
        f"- Custo esperado na validação: {val_cost * 1000:.1f} por mil eventos.",
        f"- Para comparação, o limiar de {TARGET_FPR:.0%} de falso positivo seria {fpr_thr:.4f}.",
        "",
        "## Período de teste",
        "",
        f"PR-AUC: **{metrics['pr_auc_teste']:.4f}**."
        if metrics["pr_auc_teste"] is not None
        else "",
        "",
        "Sinalizado = desafiado ou negado.",
        "",
        "| Grupo | Sessões | Eventos | Sinalizados pelo modelo de fraude | IC 95 % |",
        "|---|---|---|---|---|",
        *[rows(lbl, m, flagged) for lbl, m in groups if m.any()],
        "",
        "Cruzando com o modelo de automação que está no ar, como o motor faz:",
        "",
        "| Grupo | Sessões | Eventos | Sinalizados pela decisão final | IC 95 % |",
        "|---|---|---|---|---|",
        *[rows(lbl, m, combined) for lbl, m in groups if m.any()],
        "",
        "## Custo esperado por mil eventos no teste",
        "",
        "| Política | Custo |",
        "|---|---|",
        f"| Aprovar tudo | {nothing:.0f} |",
        f"| Só o modelo de automação | {only_auto:.0f} |",
        f"| Só o modelo de fraude | {only_fraud:.0f} |",
        f"| Os dois cruzados (o motor) | {both:.0f} |",
        "",
        "## O que o modelo usa (ganho)",
        "",
        "| Feature | Parte do ganho |",
        "|---|---|",
        *[f"| `{n}` | {g / total_gain:.1%} |" for n, g in gain[:10] if g > 0],
        "",
    ]
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "fraude.md").write_text("\n".join(lines), encoding="utf-8")

    with psycopg.connect(DSN) as conn:
        conn.execute(
            "INSERT INTO model_registry (versao, alvo, caminho, versao_features, metricas, limiares) "
            "VALUES (%s, 'fraude', %s, %s, %s, %s) ON CONFLICT (versao) DO NOTHING",
            (versao, str(out), FEATURE_VERSION, json.dumps(metrics), json.dumps(meta["limiares"])),
        )
    if a.activate:
        (MODELS / "ACTIVE_FRAUDE").write_text(versao + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"modelo: {out}" + ("  (ativo)" if a.activate else ""))


if __name__ == "__main__":
    main()
