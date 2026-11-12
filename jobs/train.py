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
from gandalf_features import FEATURE_NAMES, FEATURE_VERSION, upgrade_f1
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


# Features that measure how fast a session is in absolute terms. In this lab they
# separate the classes perfectly, but only because my scripted attackers are faster
# than my simulated people: the model would be learning the simulator's settings. A
# real attacker slows down with one line of code, and a real AI agent is slower than
# a person. They are still computed and logged, just not given to the model.
ABSOLUTE_PACE = frozenset(
    {
        "ritmo_media_ms",
        "ritmo_min_ms",
        "ritmo_frac_rapidos",
        "sessao_segundos",
        "sessao_seg_por_pagina",
    }
)
# The contact size of a tap is logged, but every simulated tap (person or script) has
# the emulator's fixed size, so the model could only learn the emulator from it.
NOT_YET_TRUSTED = frozenset({"toque_area"})
ALL_FEATURES = [n for n in FEATURE_NAMES if n not in NOT_YET_TRUSTED]
MODEL_FEATURES = [n for n in ALL_FEATURES if n not in ABSOLUTE_PACE]
# A recorded person counts for more than a simulated one: there are few of them and
# they are the only rows that did not come out of my own generator.
REAL_WEIGHT = 3.0


def load() -> pl.DataFrame:
    sql = """
        SELECT e.avaliacao_id, e.sessao_id, e.momento, e.evento, f.features,
               l.eh_automacao, l.nivel_atacante, l.perfil, l.fonte,
               d.score_automacao AS score_online, d.versao_modelo
        FROM feature_snapshots f
        JOIN labels l USING (avaliacao_id)
        JOIN events e USING (avaliacao_id)
        JOIN decisions d USING (avaliacao_id)
        WHERE f.versao_features = ANY(%s)
        ORDER BY e.momento
    """
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(
            sql.replace("f.features,", "f.features, f.versao_features,"), ([FEATURE_VERSION, "f1"],)
        ).fetchall()
    # f1 vectors predate touch telemetry; they are upgraded by a documented rule.
    rows = [(*r[:4], r[4] if r[5] == FEATURE_VERSION else upgrade_f1(r[4]), *r[6:]) for r in rows]
    if not rows:
        raise SystemExit("no labelled data: run simulator.run, then simulator.labels")
    cols = ("avaliacao_id sessao_id momento evento features y nivel perfil fonte "
            "score_online versao_online").split()  # fmt: skip
    df = pl.DataFrame([dict(zip(cols, r, strict=True)) for r in rows], infer_schema_length=None)
    feats = pl.DataFrame(
        [{n: r[4].get(n) for n in FEATURE_NAMES} for r in rows],
        schema={n: pl.Float64 for n in FEATURE_NAMES},
    )
    df = pl.concat([df.drop("features"), feats], how="horizontal")
    return df.with_columns(pl.col("fonte").str.starts_with("gravacao").alias("real"))


def people(real: pl.DataFrame) -> list[str]:
    return sorted(real["perfil"].unique().to_list())


def assign_people(real: pl.DataFrame, names: list[str]) -> pl.DataFrame:
    """Recorded people are split by PERSON, never by event: about one in four goes to
    validation (to set the threshold), the rest to training."""
    calib = set(names[1::4])
    return real.with_columns(
        pl.when(pl.col("perfil").is_in(list(calib)))
        .then(pl.lit("validacao"))
        .otherwise(pl.lit("treino"))
        .alias("conjunto")
    )


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


def xy(df: pl.DataFrame, feats: list[str] | None = None) -> tuple[np.ndarray, np.ndarray]:
    return df.select(feats or MODEL_FEATURES).to_numpy(), df["y"].to_numpy().astype(int)


@dataclass
class Model:
    booster: lgb.Booster
    a: float
    b: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        raw = self.booster.predict(x, raw_score=True)
        return 1.0 / (1.0 + np.exp(-(self.a * raw + self.b)))


def fit(train: pl.DataFrame, valid: pl.DataFrame, feats: list[str] | None = None) -> Model:
    feats = feats or MODEL_FEATURES
    xt, yt = xy(train, feats)
    xv, yv = xy(valid, feats)
    params = PARAMS | {"scale_pos_weight": float((yt == 0).sum()) / max(int((yt == 1).sum()), 1)}
    weights = np.where(train["real"].to_numpy(), REAL_WEIGHT, 1.0)
    booster = lgb.train(
        params,
        lgb.Dataset(xt, yt, weight=weights, feature_name=feats),
        num_boost_round=500,
        valid_sets=[lgb.Dataset(xv, yv)],
        feval=lambda p, d: ("pr_auc", average_precision_score(d.get_label(), p), True),
        callbacks=[lgb.early_stopping(40, first_metric_only=False, verbose=False)],
    )
    # Platt scaling on validation: class weights distort the raw probabilities.
    raw = booster.predict(xv, raw_score=True, num_iteration=booster.best_iteration)
    # Regularised: on a nearly separable validation set an unregularised fit collapses
    # every score to 0 or 1 and the thresholds stop meaning anything.
    platt = LogisticRegression(C=0.05).fit(raw.reshape(-1, 1), yv)
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
    for pf in sorted(set(perfil[y == 1])):
        m = (y == 1) & (perfil == pf)
        out.append(rate_row(f"Ferramenta: {pf} (detecção)", flagged[m], sess[m], rng))
    out.append(rate_row("Humanos, todos (falso positivo)", flagged[y == 0], sess[y == 0], rng))
    real = (y == 0) & np.char.startswith(perfil.astype(str), "real_")
    if real.any():
        sim = (y == 0) & ~real
        out.append(
            rate_row("Humanos REAIS gravados (falso positivo)", flagged[real], sess[real], rng)
        )
        out.append(rate_row("Humanos simulados (falso positivo)", flagged[sim], sess[sim], rng))
    for pf in sorted(set(perfil[y == 0])):
        m = (y == 0) & (perfil == pf)
        out.append(rate_row(f"Humano: {pf} (falso positivo)", flagged[m], sess[m], rng))
    return [*out, ""]


def leave_one_person_out(
    sim_train: pl.DataFrame, sim_valid: pl.DataFrame, real: pl.DataFrame, rng
) -> list[str]:  # type: ignore[no-untyped-def]
    """How the model treats a person it has never seen: each recorded participant is
    held out in turn, the model is refitted on everyone else and applied to them."""
    names = people(real)
    if len(names) < 3:
        return ["Menos de 3 participantes gravados: avaliação por pessoa não realizada.", ""]
    flags, sess, who, phone = [], [], [], []
    for held in names:
        rest = assign_people(real.filter(pl.col("perfil") != held), [n for n in names if n != held])
        train = pl.concat([sim_train, rest.filter(pl.col("conjunto") == "treino")], how="diagonal")
        valid = pl.concat(
            [sim_valid, rest.filter(pl.col("conjunto") == "validacao")], how="diagonal"
        )
        m = fit(train, valid)
        xv, yv = xy(valid)
        thr = threshold_at_fpr(m.predict(xv)[yv == 0], TARGET_FPR)
        out = real.filter(pl.col("perfil") == held)
        flags.append(m.predict(xy(out)[0]) >= thr)
        sess.append(out["sessao_id"].to_numpy())
        who.append(out["perfil"].to_numpy())
        phone.append(out["toque"].fill_null(0).to_numpy() >= 0.5)
    f, g, w, ph = (np.concatenate(a) for a in (flags, sess, who, phone))
    lines = [
        "| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |",
        "|---|---|---|---|---|",
        rate_row("**Todas as pessoas reais**", f, g, rng),
        rate_row("No computador (mouse)", f[~ph], g[~ph], rng),
        rate_row("No celular (toque)", f[ph], g[ph], rng),
    ]
    lines += [
        rate_row(f"Participante {n.removeprefix('real_')}", f[w == n], g[w == n], rng)
        for n in names
    ]
    return [*lines, ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--activate", action="store_true", help="point models/ACTIVE at the new model")
    a = ap.parse_args()
    rng = np.random.default_rng(7)

    everything = load()
    sim = temporal_split(everything.filter(~pl.col("real")))
    real = everything.filter(pl.col("real"))
    real = assign_people(real, people(real))
    df = pl.concat([sim, real], how="diagonal")
    sim_train, sim_valid = (sim.filter(pl.col("conjunto") == c) for c in ("treino", "validacao"))
    # Simulated sessions: oldest for training, most recent for testing. Recorded
    # people: by person. The test set holds simulated traffic only; how the model
    # treats a real person is measured separately, leaving one person out at a time.
    train, valid, test = (
        df.filter(pl.col("conjunto") == c) for c in ("treino", "validacao", "teste")
    )
    model = fit(train, valid)

    xv, yv = xy(valid)
    sv = model.predict(xv)
    desafiar = threshold_at_fpr(sv[yv == 0], TARGET_FPR)
    # Deny only above every legitimate score seen in validation.
    # ...and never below 0.99: with this little validation data a wrong challenge is
    # the affordable mistake, a wrong denial is not.
    negar = float(min(max(np.nextafter(sv[yv == 0].max(), 1.0), desafiar, 0.99), 1.0))

    xt, _ = xy(test)

    # Ablation: the same model, also given the absolute-pace features.
    full = fit(train, valid, ALL_FEATURES)
    full_valid = full.predict(xy(valid, ALL_FEATURES)[0])
    full_thr = threshold_at_fpr(full_valid[yv == 0], TARGET_FPR)
    full_test = full.predict(xy(test, ALL_FEATURES)[0])
    full_gain = sorted(
        zip(ALL_FEATURES, full.booster.feature_importance("gain"), strict=True),
        key=lambda kv: -kv[1],
    )
    pace_share = sum(g for n, g in full_gain if n in ABSOLUTE_PACE) / (
        sum(g for _, g in full_gain) or 1.0
    )
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
        "features": MODEL_FEATURES,
        "calibracao": {"a": model.a, "b": model.b},
        "limiares": {"desafiar": desafiar, "negar": negar},
        "arvores": model.booster.best_iteration,
        "metricas": metrics,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    gain = sorted(
        zip(MODEL_FEATURES, model.booster.feature_importance("gain"), strict=True),
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
        *evaluate("Modelo LightGBM (sem features de ritmo absoluto)", test, st, desafiar, rng),
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
        "## Pessoas reais: uma de fora por vez",
        "",
        f"{real['perfil'].n_unique()} voluntários gravados "
        f"({real['sessao_id'].n_unique()} sessões, {real.height} eventos). Para cada um, o modelo é retreinado **sem nenhuma sessão dessa "
        "pessoa** e depois aplicado a ela. É a estimativa de falso positivo para alguém que o "
        "sistema nunca viu. O modelo final treina com cerca de três quartos das pessoas e "
        "usa as demais para escolher o limiar.",
        "",
        *leave_one_person_out(sim_train, sim_valid, real, rng),
        "## Ablação: e se o modelo visse a velocidade absoluta?",
        "",
        f"Com as {len(ABSOLUTE_PACE)} features de ritmo absoluto (média e mínimo dos intervalos, "
        "tempo de sessão), elas respondem por "
        f"**{pace_share:.0%} do ganho** e a separação fica quase perfeita. Esse resultado "
        "**não é usado**: ele mede que os atacantes do laboratório são mais rápidos que as "
        "pessoas simuladas, o que é uma escolha minha de parâmetros, não uma propriedade de "
        "atacantes. O modelo em produção não recebe essas features.",
        "",
        *evaluate("Com ritmo absoluto (descartado)", test, full_test, full_thr, rng),
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
