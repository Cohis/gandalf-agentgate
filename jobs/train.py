"""Train and evaluate the 'is this automation?' model.

    uv run python -m jobs.train            # train, evaluate, write the report
    uv run python -m jobs.train --activate # ...and make it the engine's active model

Rules this job follows:
- Features are read from ``feature_snapshots``: the exact vectors serving computed,
  before any label existed. Nothing is recomputed here.
- The split is temporal and by session: train on the oldest sessions, validate on
  the next, test on the most recent. A session never straddles two sets.
- Operating thresholds are chosen on validation and only then applied to test.
- Accuracy is never reported: the classes are imbalanced and the costs asymmetric.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import psycopg
from gandalf_features import FEATURE_NAMES, FEATURE_VERSION
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

DSN = os.environ.get("GANDALF_DATABASE_URL", "postgresql://gandalf:gandalf@localhost:5432/gandalf")
MODELS = Path("models")
REPORT = Path("eval/out")
TARGET_FPR = 0.01
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 10,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": 7,
    "num_threads": 4,
}


def load() -> pl.DataFrame:
    sql = """
        SELECT e.avaliacao_id, e.sessao_id, e.momento, e.evento, f.features,
               l.eh_automacao, l.nivel_atacante, l.perfil, l.fonte,
               d.score_automacao AS score_online, d.versao_modelo
        FROM feature_snapshots f
        JOIN labels l USING (avaliacao_id)
        JOIN events e USING (avaliacao_id)
        JOIN decisions d USING (avaliacao_id)
        WHERE f.versao_features = %s
        ORDER BY e.momento
    """
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(sql, (FEATURE_VERSION,)).fetchall()
    if not rows:
        raise SystemExit("no labelled data: run simulator.run, then simulator.labels")
    cols = ("avaliacao_id sessao_id momento evento features y nivel perfil fonte "
            "score_online versao_online").split()  # fmt: skip
    df = pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], infer_schema_length=None)
    feats = pl.DataFrame(
        [{n: r[4].get(n) for n in FEATURE_NAMES} for r in rows],
        schema={n: pl.Float64 for n in FEATURE_NAMES},
    )
    return pl.concat([df.drop("features"), feats], how="horizontal")


def temporal_split(df: pl.DataFrame) -> pl.DataFrame:
    """Each session goes to the set of its first event: 60 % / 20 % / 20 % in time."""
    first = df.group_by("sessao_id").agg(pl.col("momento").min().alias("inicio")).sort("inicio")
    n = first.height
    first = first.with_row_index("ordem").with_columns(
        pl.when(pl.col("ordem") < int(n * 0.6))
        .then(pl.lit("treino"))
        .when(pl.col("ordem") < int(n * 0.8))
        .then(pl.lit("validacao"))
        .otherwise(pl.lit("teste"))
        .alias("conjunto")
    )
    return df.join(first.select("sessao_id", "conjunto"), on="sessao_id")


def xy(df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    return df.select(FEATURE_NAMES).to_numpy(), df["y"].to_numpy().astype(int)


@dataclass
class Model:
    booster: lgb.Booster
    a: float
    b: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        raw = self.booster.predict(x, raw_score=True)
        return 1.0 / (1.0 + np.exp(-(self.a * raw + self.b)))


def fit(train: pl.DataFrame, valid: pl.DataFrame) -> Model:
    xt, yt = xy(train)
    xv, yv = xy(valid)
    params = PARAMS | {"scale_pos_weight": float((yt == 0).sum()) / max(int((yt == 1).sum()), 1)}
    booster = lgb.train(
        params,
        lgb.Dataset(xt, yt, feature_name=list(FEATURE_NAMES)),
        num_boost_round=500,
        valid_sets=[lgb.Dataset(xv, yv)],
        feval=lambda p, d: ("pr_auc", average_precision_score(d.get_label(), p), True),
        callbacks=[lgb.early_stopping(40, first_metric_only=False, verbose=False)],
    )
    # Platt scaling on validation: class weights distort the raw probabilities.
    raw = booster.predict(xv, raw_score=True, num_iteration=booster.best_iteration)
    platt = LogisticRegression(C=1e6).fit(raw.reshape(-1, 1), yv)
    return Model(booster, float(platt.coef_[0, 0]), float(platt.intercept_[0]))


def threshold_at_fpr(scores_neg: np.ndarray, fpr: float) -> float:
    """Smallest threshold whose false-positive rate on ``scores_neg`` is <= fpr."""
    ordered = np.sort(scores_neg)[::-1]
    k = int(np.floor(fpr * len(ordered)))  # negatives allowed above the threshold
    return float(np.nextafter(ordered[k], 1.0)) if k < len(ordered) else 0.0


def boot_ci(flags: np.ndarray, groups: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """95 % interval for a rate, resampling whole sessions (events in one are not
    independent)."""
    ids = np.unique(groups)
    if len(ids) < 2:
        return (float("nan"), float("nan"))
    by = {g: flags[groups == g] for g in ids}
    rates = []
    for _ in range(1000):
        sample = np.concatenate([by[g] for g in rng.choice(ids, len(ids))])
        rates.append(sample.mean())
    lo, hi = np.percentile(rates, [2.5, 97.5])
    return float(lo), float(hi)


def rate_row(name: str, flags: np.ndarray, groups: np.ndarray, rng) -> str:  # type: ignore[no-untyped-def]
    if len(flags) == 0:
        return f"| {name} | 0 | 0 | — | — |"
    lo, hi = boot_ci(flags, groups, rng)
    return (
        f"| {name} | {len(np.unique(groups))} | {len(flags)} | {flags.mean():.1%} | "
        f"{lo:.1%} a {hi:.1%} |"
    )


def evaluate(name: str, test: pl.DataFrame, scores: np.ndarray, thr: float, rng) -> list[str]:  # type: ignore[no-untyped-def]
    y = test["y"].to_numpy().astype(int)
    nivel = test["nivel"].to_numpy()
    perfil = test["perfil"].to_numpy()
    sess = test["sessao_id"].to_numpy()
    flagged = scores >= thr
    thr_test = threshold_at_fpr(scores[y == 0], TARGET_FPR)
    out = [
        f"### {name}",
        "",
        f"- PR-AUC: **{average_precision_score(y, scores):.4f}** · ROC-AUC: "
        f"{roc_auc_score(y, scores):.4f}",
        f"- Recall com FPR fixa de {TARGET_FPR:.0%} no próprio teste: "
        f"**{(scores[y == 1] >= thr_test).mean():.1%}**",
        f"- Com o limiar escolhido na validação ({thr:.4f}): recall "
        f"{flagged[y == 1].mean():.1%}, clientes bons barrados {flagged[y == 0].mean():.1%}",
        "",
        "| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |",
        "|---|---|---|---|---|",
    ]
    for lv in sorted(set(nivel[y == 1])):
        m = nivel == lv
        out.append(rate_row(f"Atacante nível {lv} (detecção)", flagged[m], sess[m], rng))
    out.append(rate_row("Humanos, todos (falso positivo)", flagged[y == 0], sess[y == 0], rng))
    for pf in sorted(set(perfil[y == 0])):
        m = (y == 0) & (perfil == pf)
        out.append(rate_row(f"Humano: {pf} (falso positivo)", flagged[m], sess[m], rng))
    return [*out, ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--activate", action="store_true", help="point models/ACTIVE at the new model")
    a = ap.parse_args()
    rng = np.random.default_rng(7)

    df = temporal_split(load())
    train, valid, test = (
        df.filter(pl.col("conjunto") == c) for c in ("treino", "validacao", "teste")
    )
    model = fit(train, valid)

    xv, yv = xy(valid)
    sv = model.predict(xv)
    desafiar = threshold_at_fpr(sv[yv == 0], TARGET_FPR)
    # Deny only above every legitimate score seen in validation.
    negar = float(min(max(np.nextafter(sv[yv == 0].max(), 1.0), desafiar), 1.0))

    xt, _ = xy(test)
    st = model.predict(xt)

    # Generalisation to an attacker family never seen in training.
    seen = train.filter(pl.col("nivel") != 2), valid.filter(pl.col("nivel") != 2)
    blind = fit(*seen)
    blind_thr = threshold_at_fpr(blind.predict(xy(seen[1])[0])[xy(seen[1])[1] == 0], TARGET_FPR)
    l2 = test.filter(pl.col("nivel") == 2)
    blind_recall = (
        float((blind.predict(xy(l2)[0]) >= blind_thr).mean()) if l2.height else float("nan")
    )

    stamp = time.strftime("%Y%m%d-%H%M")
    digest = hashlib.sha256(model.booster.model_to_string().encode()).hexdigest()[:6]
    versao = f"{stamp}-{digest}"
    out_dir = MODELS / versao
    out_dir.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(out_dir / "model.txt", num_iteration=model.booster.best_iteration)
    y_test = test["y"].to_numpy().astype(int)
    metrics = {
        "pr_auc_teste": float(average_precision_score(y_test, st)),
        "recall_teste": float((st[y_test == 1] >= desafiar).mean()),
        "fpr_teste": float((st[y_test == 0] >= desafiar).mean()),
        "recall_nivel2_sem_ver_nivel2": blind_recall,
        "linhas": {"treino": train.height, "validacao": valid.height, "teste": test.height},
    }
    meta = {
        "versao": versao,
        "alvo": "automacao",
        "versao_features": FEATURE_VERSION,
        "features": list(FEATURE_NAMES),
        "calibracao": {"a": model.a, "b": model.b},
        "limiares": {"desafiar": desafiar, "negar": negar},
        "arvores": model.booster.best_iteration,
        "metricas": metrics,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    gain = sorted(
        zip(FEATURE_NAMES, model.booster.feature_importance("gain"), strict=True),
        key=lambda kv: -kv[1],
    )
    total_gain = sum(g for _, g in gain) or 1.0
    counts = (
        df.group_by("conjunto", "nivel")
        .agg(pl.len().alias("eventos"), pl.col("sessao_id").n_unique().alias("sessoes"))
        .sort("conjunto", "nivel")
    )
    span = df["momento"].max() - df["momento"].min()
    lines = [
        f"# Fase 2: modelo de automação `{versao}`",
        "",
        f"Gerado em {time.strftime('%Y-%m-%d %H:%M')}. Features `{FEATURE_VERSION}`, "
        f"{model.booster.best_iteration} árvores. Dados: {df.height} eventos em "
        f"{df['sessao_id'].n_unique()} sessões, cobrindo {span.total_seconds() / 86400:.1f} dias "
        "simulados.",
        "",
        "Todo o tráfego avaliado aqui passou por um navegador real ou por um cliente HTTP "
        "real contra a loja. Os humanos são simulados (trajetórias de mouse reais, ritmo e "
        "digitação modelados). Leia `docs/limitations.md` antes de citar estes números.",
        "",
        "## Divisão temporal",
        "",
        "| Conjunto | Nível | Sessões | Eventos |",
        "|---|---|---|---|",
        *[
            f"| {r['conjunto']} | {r['nivel']} | {r['sessoes']} | {r['eventos']} |"
            for r in counts.iter_rows(named=True)
        ],
        "",
        "## Resultado no período de teste (o mais recente)",
        "",
        *evaluate("Modelo LightGBM", test, st, desafiar, rng),
    ]
    base = test.filter(pl.col("versao_online") == "heuristica-0")
    if base.height == test.height:
        sb_valid = valid["score_online"].to_numpy()
        thr_b = threshold_at_fpr(sb_valid[yv == 0], TARGET_FPR)
        lines += evaluate(
            "Linha de base: heurística escrita à mão",
            test,
            test["score_online"].to_numpy(),
            thr_b,
            rng,
        )
    lines += [
        "## Generalização",
        "",
        f"Treinando **sem nenhum exemplo do nível 2** e testando só no nível 2: detecção de "
        f"**{blind_recall:.1%}** ({l2.height} eventos), com o limiar de {TARGET_FPR:.0%} de falso "
        "positivo. É a medida de quanto o modelo pega um atacante que nunca viu.",
        "",
        "## O que o modelo usa (ganho)",
        "",
        "| Feature | Parte do ganho |",
        "|---|---|",
        *[f"| `{n}` | {g / total_gain:.1%} |" for n, g in gain[:12] if g > 0],
        "",
        "## Limiares de operação",
        "",
        f"- Desafiar a partir de {desafiar:.4f} ({TARGET_FPR:.0%} de falso positivo na validação).",
        f"- Negar a partir de {negar:.4f} (acima de todo score legítimo visto na validação).",
        "",
    ]
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "fase2.md").write_text("\n".join(lines), encoding="utf-8")

    with psycopg.connect(DSN) as conn:
        conn.execute(
            "INSERT INTO model_registry "
            "(versao, alvo, caminho, versao_features, metricas, limiares) "
            "VALUES (%s, 'automacao', %s, %s, %s, %s) ON CONFLICT (versao) DO NOTHING",
            (
                versao,
                str(out_dir),
                FEATURE_VERSION,
                json.dumps(metrics),
                json.dumps(meta["limiares"]),
            ),
        )
        if a.activate:
            conn.execute("UPDATE model_registry SET ativo = (versao = %s)", (versao,))
    if a.activate:
        (MODELS / "ACTIVE").write_text(versao + "\n", encoding="utf-8")

    print("\n".join(lines[lines.index("## Resultado no período de teste (o mais recente)") :]))
    print(f"modelo: {out_dir}" + ("  (ativo)" if a.activate else ""))


if __name__ == "__main__":
    main()
