"""Scorers return a probability plus additive per-feature contributions (log-odds).

Phase 1 ships a transparent hand-written baseline. Phase 2 swaps in LightGBM, whose
``predict(pred_contrib=True)`` yields the same shape (TreeSHAP), so the explanation
path does not change.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gandalf_features import COMPATIBLE_VERSIONS, FEATURE_VERSION, FeatureVector

Contributions = dict[str, float]


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Operating points chosen offline on the validation period."""

    desafiar: float
    negar: float


class Scorer(Protocol):
    version: str
    thresholds: Thresholds | None

    def score(self, f: FeatureVector) -> tuple[float, Contributions]: ...


def _v(f: FeatureVector, name: str) -> float:
    v = f.get(name)
    return 0.0 if v is None else v


class HeuristicScorer:
    """'Is this automation?' baseline. Also the yardstick the trained model must beat."""

    version = "heuristica-0"
    thresholds: Thresholds | None = None  # uses the policy file's defaults
    _BIAS = -3.0

    def score(self, f: FeatureVector) -> tuple[float, Contributions]:
        c: Contributions = {}
        if not _v(f, "tel_coletada"):
            c["tel_coletada"] = 3.0
        if _v(f, "webdriver"):
            c["webdriver"] = 3.5
        if _v(f, "ua_automacao"):
            c["ua_automacao"] = 3.0
        if _v(f, "inconsistencias_fp"):
            c["inconsistencias_fp"] = min(_v(f, "inconsistencias_fp"), 5.0) * 0.6
        if _v(f, "rede_datacenter"):
            c["rede_datacenter"] = 1.2
        if _v(f, "rede_tor"):
            c["rede_tor"] = 1.5
        cv = f.get("ritmo_cv")
        if cv is not None and cv < 0.15:
            c["ritmo_cv"] = 1.5
        lin = f.get("cursor_linear")
        if lin is not None and lin > 0.95:
            c["cursor_linear"] = 1.2
        centro = f.get("cliques_centro")
        if centro is not None and centro > 0.9:
            c["cliques_centro"] = 1.0
        if _v(f, "digitacao_constante"):
            c["digitacao_constante"] = 1.0
        if _v(f, "honeypot"):
            c["honeypot"] = 1.5
        if _v(f, "canario"):
            c["canario"] = 1.5
        contas = _v(f, "contas_por_disp_24h")
        if contas >= 3:
            c["contas_por_disp_24h"] = min(contas * 0.5, 3.0)
        if _v(f, "ev_ip_10m") >= 30:
            c["ev_ip_10m"] = 1.5
        logit = self._BIAS + sum(c.values())
        return 1.0 / (1.0 + math.exp(-logit)), c


class ModelError(RuntimeError):
    pass


class LightGBMScorer:
    """Gradient-boosted trees. Contributions come from LightGBM's built-in TreeSHAP
    (``pred_contrib``), so a decision is explained by the model that made it."""

    def __init__(self, model_dir: Path) -> None:
        import lightgbm as lgb
        import numpy as np

        meta: dict[str, Any] = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
        if meta["versao_features"] not in COMPATIBLE_VERSIONS:
            # A model must only ever see the feature set it was trained on.
            raise ModelError(
                f"model {meta['versao']} was trained on features {meta['versao_features']}, "
                f"but this engine computes {FEATURE_VERSION}"
            )
        self._np = np
        self._booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
        self._names: list[str] = meta["features"]
        self._a, self._b = meta["calibracao"]["a"], meta["calibracao"]["b"]
        self.version: str = meta["versao"]
        lim = meta.get("limiares")
        self.thresholds: Thresholds | None = (
            Thresholds(float(lim["desafiar"]), float(lim["negar"])) if lim else None
        )

    def score(self, f: FeatureVector) -> tuple[float, Contributions]:
        nan = float("nan")
        row = self._np.array(
            [[nan if (v := f.get(n)) is None else v for n in self._names]], dtype=self._np.float64
        )
        contrib = self._booster.predict(row, pred_contrib=True)[0]
        raw = float(contrib.sum())  # last column is the expected value
        prob = 1.0 / (1.0 + math.exp(-(self._a * raw + self._b)))  # Platt calibration
        return prob, {n: float(c) for n, c in zip(self._names, contrib[:-1], strict=True)}


def load_scorer(model_dir: Path | None) -> Scorer:
    """The model named in ``<model_dir>/ACTIVE``, or the heuristic baseline if none."""
    if model_dir is None:
        return HeuristicScorer()
    active = model_dir / "ACTIVE"
    if not active.exists():
        return HeuristicScorer()
    return LightGBMScorer(model_dir / active.read_text(encoding="utf-8").strip())


def load_fraud_scorer(model_dir: Path | None) -> Scorer | None:
    """The 'is this fraud?' model named in ``<model_dir>/ACTIVE_FRAUDE``, if any.
    There is no hand-written fallback: without a trained model the engine simply
    does not score fraud (score_fraude stays null)."""
    if model_dir is None:
        return None
    active = model_dir / "ACTIVE_FRAUDE"
    if not active.exists():
        return None
    return LightGBMScorer(model_dir / active.read_text(encoding="utf-8").strip())
